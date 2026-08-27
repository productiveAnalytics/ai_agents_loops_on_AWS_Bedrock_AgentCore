# Number-Guessing Multi-Agent Loop on AWS Bedrock AgentCore

A production-style port of the [`ai_agents_loops`](../ai_agents_loops) number-guessing
game (LangChain + LangGraph) onto Amazon Bedrock AgentCore. Three fully isolated
Runtime agents (Working, Inspector, Orchestrator), two IAM-isolated Gateways, short-term
Memory, a digit-blocking Guardrail, a code-based Evaluator, and automatic Observability.
See [PRD.md](PRD.md) for the full design.

**Status: deployed and verified working on AWS** (account `778858743114`, region `us-west-2`,
project `agentcorecli`). A full 8-loop game has run end-to-end with no crashes, and the
IAM isolation between the two agents has been independently verified via
`iam:SimulatePrincipalPolicy` in both directions (see [Troubleshooting](#troubleshooting--problems-encountered-during-live-deployment)
below for everything that had to be fixed to get there — worth reading before you
redeploy from scratch).

## Prerequisites

- AWS account with credentials configured (this project targets profile `prod8ctive`,
  account `778858743114`, region `us-west-2` — see `shared/resource_config.py`)
- Bedrock model access enabled for `us.anthropic.claude-haiku-4-5-20251001-v1:0` in that
  region (confirmed already enabled for this account)
- Node.js 20+ (for `npx @aws/agentcore`) and Docker (for the Runtime container builds)
- Python 3.12+, [uv](https://docs.astral.sh/uv/)

## 1. Install local dependencies and run the tests

```bash
uv sync
uv run pytest
```

This runs `tests/test_iam_boundary.py` and `tests/test_no_digit_leak_local.py` against
`shared/` directly — no AWS needed.

## 2. Deploy the 3 Lambdas (first AWS-mutating step)

```bash
uv run python deploy/agentcore-cli/deploy_lambdas.py
```

Creates one IAM execution role (`number-guessing-lambda-execution-role`) and three Lambda
functions (`number-guessing-generate-guess`, `number-guessing-read-secret`,
`number-guessing-no-secret-leak-evaluator`), all tagged `Project=number_guessing_ai_agents`.
Writes their ARNs to `deploy/agentcore-cli/.deployed_lambdas.json` (not committed).

## 3. Wire the Gateway targets and Evaluator to those Lambdas

Run the `agentcore add gateway-target` (x2) and `agentcore add evaluator` commands from
[`deploy/agentcore-cli/bootstrap_commands.sh`](deploy/agentcore-cli/bootstrap_commands.sh),
using the ARNs from step 2. These only edit the local `agentcore.json`, no AWS calls yet.
See [Troubleshooting](#troubleshooting--problems-encountered-during-live-deployment) for
two non-obvious gotchas here (`--outbound-auth` and the tool-schema-file format).

## 4. Create the Guardrail

```bash
uv run python deploy/agentcore-cli/post_deploy/create_guardrail.py
```

Creates the Bedrock Guardrail that blocks any digit in the Inspector's output. Copy the
printed `GUARDRAIL_ID`/`GUARDRAIL_VERSION`/`GUARDRAIL_BLOCKED_MESSAGE` into the Inspector
Agent's `envVars` in `agentcore.json`.

## 5. Hand-author the remaining `agentcore.json` wiring

`connections` (Runtime → Gateway/Memory IAM access) and the remaining `envVars`
(`BEDROCK_MODEL_ID`, `AWS_REGION`, `MAX_LOOPS_PARAM_NAME`, the Working/Inspector Runtime
ARNs for the Orchestrator) aren't yet exposed as non-interactive CLI flags, so these are
edited directly into `deploy/agentcore-cli/agentcore/agentcore.json` against the schema
documented in `deploy/agentcore-cli/agentcore/.llm-context/agentcore.ts`. Then:

```bash
cd deploy/agentcore-cli/agentcore
npx @aws/agentcore validate
```

## 6. Deploy — this is a two-phase process, not one command

**Phase 1** — deploy runtimes + gateways (+ targets) + memory + evaluator with **no**
`connections` yet (Gateway/Runtime ARNs don't exist before creation, so nothing can
reference them):

```bash
cd deploy/agentcore-cli
AWS_PROFILE=prod8ctive npx @aws/agentcore deploy -y
```

Then pull the real ARNs it just created:

```bash
AWS_PROFILE=prod8ctive npx @aws/agentcore status --json
```

**Phase 2** — hand-fill those ARNs into `deploy/agentcore-cli/patch_agentcore_json_phase2.py`
(the `WORKING_RUNTIME_ARN`/`INSPECTOR_RUNTIME_ARN`/`WORKING_ROLE_ARN`/`WORKING_GATEWAY_ARN`/
`INSPECTOR_GATEWAY_ARN` constants near the top), run it, `agentcore validate`, then deploy
again:

```bash
uv run python deploy/agentcore-cli/patch_agentcore_json_phase2.py
cd deploy/agentcore-cli/agentcore && npx @aws/agentcore validate
cd .. && AWS_PROFILE=prod8ctive npx @aws/agentcore deploy -y
```

This second deploy is also where the explicit IAM Deny statements (see Troubleshooting)
and the `AWS_PROFILE` env var (the CLI doesn't read it from `aws-targets.json`) matter —
always pass `AWS_PROFILE=<profile>` explicitly on every `deploy`/`invoke`/`logs`/`status`
call. Review `npx @aws/agentcore deploy --diff` first if you want to see exactly what
will change before committing to either phase.

## 7. Seed the secret

```bash
uv run python scripts/setup_secret.py --secret-value 53 --max-loops 8
```

## 8. Play a game

```bash
npx @aws/agentcore invoke --runtime number_guessing_orchestrator_agent
```

## 9. Tear it down

```bash
uv run python scripts/teardown.py           # dry run - lists everything tagged for this project
uv run python scripts/teardown.py --yes      # actually deletes it
```

Finds every resource tagged `Project=number_guessing_ai_agents` (Lambdas, IAM role,
Runtimes, Gateways, Memory, Guardrail, SSM parameters) via the Resource Groups Tagging
API and deletes it — independent of `agentcore.json`'s local state, so it works even if
that file is stale.

## Troubleshooting — problems encountered during live deployment

Everything below was hit for real getting this project from "code complete" to "actually
running on AWS," in the order encountered. Kept here because several of these are
non-obvious platform behaviors (not just typos) that will bite again on a fresh deploy or
a similar AgentCore project.

### 1. `codeLocation` resolves relative to the project root, not to `agentcore.json`'s own directory

**Symptom:** `agentcore deploy` failed with `Dockerfile not found at
/home/.../claude_workspaces/app/working_agent/Dockerfile` — note the path is missing the
repo name, i.e. it resolved one directory too high.

**Root cause:** `agentcore add agent --code-location ../../../app/working_agent` (run
from inside `deploy/agentcore-cli/agentcore/`) stores that string in `agentcore.json`
verbatim, but `agentcore deploy` resolves `codeLocation` relative to the **project root**
(`deploy/agentcore-cli/`, one level above `agentcore/`) — not relative to where you ran
`add agent` from, and not relative to `agentcore.json`'s own directory.

**Fix:** the path needs one fewer `../` than you'd naively use from inside `agentcore/`.
From the project root, `app/working_agent` is `../../app/working_agent`.

### 2. `AWS_PROFILE` must be exported explicitly on every CLI call

**Symptom:** `AWS credentials are invalid.`

**Root cause:** `aws-targets.json` only carries `account`/`region`, not a profile name —
the CLI falls back to whatever the ambient AWS SDK credential chain resolves (which may
not be `prod8ctive`).

**Fix:** always prefix `agentcore deploy`/`invoke`/`logs`/`status` with
`AWS_PROFILE=prod8ctive` explicitly.

### 3. A failed changeset leaves a stuck `REVIEW_IN_PROGRESS` CloudFormation stack

**Symptom:** the *next* `agentcore deploy` attempt (after fixing whatever caused the
first one to fail validation) errors with `Stack "AgentCore-agentcorecli-default" is
currently in REVIEW_IN_PROGRESS state. Please wait for the operation to complete before
deploying.` — and it never completes, because nothing is actually running.

**Root cause:** when CDK's `create-change-set --change-set-type CREATE` fails validation
before creating any real resources, CloudFormation still leaves the stack shell behind in
`REVIEW_IN_PROGRESS`.

**Fix:** delete the empty stack shell, then redeploy:
```python
import boto3
boto3.Session(profile_name="prod8ctive", region_name="us-west-2") \
    .client("cloudformation").delete_stack(StackName="AgentCore-agentcorecli-default")
```

### 4. Gateway target names must be hyphen-only; Evaluator names get project-prefixed too

**Symptom:** `CDK deploy failed` with two `AWS::BedrockAgentCore::*` CFN validation
errors:
- `Property value [read_secret] does not match pattern: ^([0-9a-zA-Z][-]?){1,100}$` on a
  `GatewayTarget`'s `Name`
- `Property value [agentcorecli_number_guessing_no_secret_leak_evaluator] does not match
  pattern: ^[a-zA-Z][a-zA-Z0-9_]{0,47}$` on an `Evaluator`'s `EvaluatorName`

**Root cause:** (a) Gateway **target** names (not gateway names, and not the MCP tool
name inside the schema file) allow hyphens only, no underscores — different from the
runtime/memory/evaluator name pattern (`^[a-zA-Z][a-zA-Z0-9_]{0,47}$`, underscores only).
(b) Evaluator names get silently prefixed with `<project-name>_` at deploy time, the same
way Gateway names get `<project-name>-` prefixed — a name that's fine on its own can
still blow the 48-char limit once prefixed.

**Fix:** target names use hyphens (`generate-guess`, `read-secret`); keep the evaluator's
own name short enough to leave room for the project-name prefix
(`no_secret_leak_evaluator`, not the full `number_guessing_no_secret_leak_evaluator`).

### 5. Gateway MCP tools are namespaced as `${target_name}___${tool_name}`

**Symptom:** Working/Inspector Agent crashed with `StopIteration` from
`next(t for t in tools if t.name == "generate_guess")` — the tool the agent was looking
for was never in the list `load_mcp_tools()` returned.

**Root cause:** [AWS's documented convention](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-tool-naming.html) —
a Gateway namespaces every tool by its target name with a **triple underscore**
separator, so a tool named `generate_guess` behind a target named `generate-guess` shows
up over MCP as `generate-guess___generate_guess`, not `generate_guess`.

**Fix:** match on the full namespaced name in application code
(`t.name == "generate-guess___generate_guess"`).

### 6. MCP tool call results are a list of content blocks, not a raw dict

**Symptom:** after fixing #5, a new crash: `KeyError: 'guess'` inside `_parse_guess`,
even though the Lambda clearly returned `{"guess": N}`.

**Root cause:** `langchain-mcp-adapters` returns the MCP `CallToolResult` content
verbatim — a list like `[{"type": "text", "text": "{\"guess\": 42}"}]` — not the Lambda's
return value merged into a plain dict. The actual payload is JSON-encoded *inside* the
`"text"` field of the first content block.

**Fix:** parsing needs to handle nested content blocks: if given a dict with a `"text"`
key (and no direct `"guess"`/`"secret_value"` key), `json.loads()` the `"text"` value and
recurse.

### 7. `@aws/agentcore-cdk` wires every in-project Gateway to every in-project Runtime, unconditionally

**This is the big one.** The whole point of using two separate Gateways was IAM
isolation (Working Agent can't reach `read_secret`) — and the platform's default behavior
undermines that by design.

**Symptom:** `guard_node` reported `cheat_detected: true` on every run, even though the
Working Agent never actually attempted to reach the Inspector's gateway.

**Root cause:** live-inspecting the Working Agent's deployed IAM role showed
`bedrock-agentcore:InvokeGateway` **Allow**ed on *both* gateway ARNs, despite only
declaring a `connections` entry for its own gateway. Reading
`@aws/agentcore-cdk`'s own source (`AgentCoreMcp.js`, `wireGatewayUrlsToAgents`)
confirms this is intentional, not a bug — its own comment says: *"In v2 schema, all
resources have implicit access, so all gateways are wired to all agents."* `connections`
only ever **adds** grants/env-vars; there is no config that opts a runtime **out** of
this automatic wiring, for gateways declared in the same project. (Memory has the exact
same "implicit access to everything in the project" behavior, confirmed separately in
`AgentCoreApplication.js`'s `wireMemoriesToAgents` — that one is intentional and fine for
this project's design, since both agents are meant to share Memory.)

**Fix:** add an explicit IAM **Deny** statement (via `additionalPolicies` →
`app/<agent>/additional-policy.json`) on the *other* agent's gateway ARN. An explicit
Deny always overrides any Allow in AWS IAM, including one the platform grants
automatically — this restores real isolation. Verified afterwards with
`iam:SimulatePrincipalPolicy` in both directions (own gateway → `allowed`, other
agent's gateway → `explicitDeny`).

**Downstream consequence:** `guard_node`'s cheat-check logic had to change to match —
checking "is the gateway ARN present in any Allow statement" is now meaningless (it's
*always* present, by design of #7). The real signal is "does an explicit Deny statement
exist for that ARN + action" — see `app/orchestrator_agent/main.py::guard_node`.

### 8. The `connections`-generated IAM grant doesn't cover the actual resource ARN AWS checks against

**Symptom:** `AccessDeniedException` on `InvokeAgentRuntime`, resource
`.../runtime/<id>/runtime-endpoint/DEFAULT` — even though a `connections` entry granting
`InvokeAgentRuntime` on the bare runtime ARN was in place and deployed.

**Root cause:** AgentCore's actual authorization check is against the runtime's
**endpoint** sub-resource (`<runtime-arn>/runtime-endpoint/DEFAULT`), but
`wire-connections.js`'s generated policy statement scopes `Resource` to the bare runtime
ARN with no endpoint suffix — a real gap between what gets granted and what gets checked.

**Fix:** add a supplementary explicit Allow (via `additionalPolicies`) for
`bedrock-agentcore:InvokeAgentRuntime`/`InvokeAgentRuntimeForUser` scoped to
`<runtime-arn>/runtime-endpoint/*` on top of the `connections`-based grant. IAM
statements are additive, so this doesn't conflict with anything.

### 9. Forgot to actually set the Lambda's own environment variables

**Symptom:** `read_secret` Lambda logs: `KeyError: 'SECRET_VALUE_PARAM_NAME'`.

**Root cause:** `agentcore add gateway-target --type lambda-function-arn` wires an
*existing* Lambda by ARN — it has no knowledge of, and doesn't configure, that Lambda's
own environment variables. Since these 3 Lambdas are deployed directly via boto3 (see
`deploy_lambdas.py`, needed because the CLI's non-interactive mode has no
deploy-from-source option), setting `Environment={"Variables": {...}}` was our own
responsibility and was simply missed on the first pass.

**Fix:** `deploy_lambdas.py` now has an explicit `ENV_VARS_BY_FUNCTION` map and passes
`Environment=` on both `create_function` and `update_function_configuration`.

### 10. Missing `bedrock:ApplyGuardrail` permission

**Symptom:** `AccessDeniedException` on `Converse`: *"not authorized to perform:
bedrock:ApplyGuardrail on resource: arn:aws:bedrock:...:guardrail/..."*

**Root cause:** attaching a Guardrail via `guardrail_config` on a `bedrock_converse`
call requires the calling role to have `bedrock:ApplyGuardrail` on that specific
guardrail ARN — this is a separate permission from `bedrock:InvokeModel`/`Converse`
itself, and isn't granted by anything in the base runtime role or by `connections`.

**Fix:** added to the Inspector's `additional-policy.json`.

### 11. Two account-level Bedrock/Anthropic gates, unrelated to IAM

Both of these returned errors that *look* like permission problems but aren't fixable
via IAM policy at all — they're AWS-account-level states:

- **"Model use case details have not been submitted for this account."** — a one-time
  form AWS requires per-account before *invoking* Anthropic models on Bedrock (separate
  from the "model access" toggle). Fix: Bedrock console → Model access → fill out the
  Anthropic use case form, wait ~15 min.
- **"Your AWS Marketplace subscription for this model cannot be completed at this
  time"** (alongside an `aws-marketplace:ViewSubscriptions`/`Subscribe` IAM error) —
  Anthropic models on Bedrock are AWS Marketplace-listed, so the calling role also needs
  those two marketplace actions (added to both agents' `additional-policy.json`), *and*
  the account itself needs a completed Marketplace subscription for the model, which can
  fail/stall independently of IAM. No API-visible way to check this state directly; if
  it persists, check the AWS Marketplace console (subscriptions) and confirm the account
  isn't under an Organizations SCP blocking `aws-marketplace:Subscribe`.

### 12. AgentCore Observability trace indexing lags behind deploys

**Symptom:** `agentcore run eval` fails with `No session spans found ... in the last 7
day(s). Has the agent been invoked?` immediately after a successful, verified invoke.

**Root cause:** AgentCore's own deploy output says it plainly: *"Transaction search
enabled. It takes ~10 minutes for transaction search to be fully active and for traces
from invocations to be indexed."* This window appears to reset on every stack update, so
several redeploys in quick succession (as happened while chasing the fixes above) can
keep pushing it back.

**Fix:** none needed — just wait and retry `agentcore run eval` a few minutes later.

## Project layout

```
app/{working_agent,inspector_agent,orchestrator_agent}/  # the 3 agents
lambdas/{generate_guess,read_secret,no_secret_leak_evaluator}/handler.py
shared/                       # resource names, IAM boundary logic, guardrail/evaluator config
deploy/agentcore-cli/
├── bootstrap_commands.sh      # every agentcore CLI command used, in order
├── deploy_lambdas.py
├── tool_schemas/
├── post_deploy/create_guardrail.py
└── agentcore/                 # the CLI project (agentcore.json, aws-targets.json, generated CDK)
scripts/{setup_secret.py,teardown.py}
tests/
```
