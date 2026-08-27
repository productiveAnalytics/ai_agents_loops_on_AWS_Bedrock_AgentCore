# Number-Guessing Multi-Agent Loop on AWS Bedrock AgentCore

A production-style port of the [`ai_agents_loops`](../ai_agents_loops) number-guessing
game (LangChain + LangGraph) onto Amazon Bedrock AgentCore. Three fully isolated
Runtime agents (Working, Inspector, Orchestrator), two IAM-isolated Gateways, short-term
Memory, a digit-blocking Guardrail, a code-based Evaluator, and automatic Observability.
See [PRD.md](PRD.md) for the full design.

**Status: code complete, not yet deployed to AWS.** Every step below that touches AWS is
called out explicitly — nothing runs automatically.

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

Run the two `agentcore add gateway-target` commands and the `agentcore add evaluator`
command from [`deploy/agentcore-cli/bootstrap_commands.sh`](deploy/agentcore-cli/bootstrap_commands.sh)
(the ones marked PENDING — they need the ARNs from step 2). These only edit the local
`agentcore.json`, no AWS calls yet.

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

## 6. Deploy everything

```bash
cd deploy/agentcore-cli/agentcore
npx @aws/agentcore deploy -y
```

This is the first genuinely billable/irreversible step — it provisions the 3 Runtime
containers, 2 Gateways, and Memory resource in your AWS account. Review `npx @aws/agentcore
deploy --diff` first if you want to see exactly what will be created.

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
