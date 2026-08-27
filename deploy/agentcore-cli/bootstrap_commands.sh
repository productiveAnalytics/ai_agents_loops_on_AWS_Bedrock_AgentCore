#!/usr/bin/env bash
# Reproducible log of every `agentcore` CLI command used to build
# deploy/agentcore-cli/agentcore/agentcore.json from scratch.
#
# Re-running this script from a clean `agentcore create --no-agent` scaffold
# reproduces the exact same agentcore.json (modulo any manual edits noted
# inline below, since a few pieces of the schema - e.g. `connections` for
# IAM/memory wiring - aren't yet exposed as non-interactive CLI flags and had
# to be hand-authored; see the comments where that happens).
#
# Run from deploy/agentcore-cli/agentcore/.
set -euo pipefail

AGENTCORE="npx --yes @aws/agentcore"

# --- Project scaffold (run once, from deploy/, creates deploy/agentcorecli/) ---
# $AGENTCORE create --project-name agentcorecli --no-agent --skip-git \
#   --skip-python-setup --skip-install --output-dir . --json
# (then: mv agentcorecli/agentcore ./agentcore, keep AGENTS.md/README.md,
#  hand-edit agentcore/aws-targets.json with account 778858743114 / us-west-2)

# --- Runtime agents (3), BYO Container, LangChain/LangGraph on Bedrock ---
$AGENTCORE add agent \
  --name number_guessing_working_agent \
  --type byo --build Container --language Python \
  --framework LangChain_LangGraph --model-provider Bedrock \
  --code-location ../../../app/working_agent --entrypoint main.py --protocol HTTP --json

$AGENTCORE add agent \
  --name number_guessing_inspector_agent \
  --type byo --build Container --language Python \
  --framework LangChain_LangGraph --model-provider Bedrock \
  --code-location ../../../app/inspector_agent --entrypoint main.py --protocol HTTP --json

$AGENTCORE add agent \
  --name number_guessing_orchestrator_agent \
  --type byo --build Container --language Python \
  --framework LangChain_LangGraph --model-provider Bedrock \
  --code-location ../../../app/orchestrator_agent --entrypoint main.py --protocol HTTP --json

# --- Short-term Memory (no strategies -> short-term only, per design) ---
$AGENTCORE add memory --name number_guessing_game_memory --json

# --- Gateways (2), IAM(SigV4) authorized, MCP protocol ---
# Names kept short: AWS caps "<project-name>-<gateway-name>" at 48 chars.
$AGENTCORE add gateway \
  --name num-guess-working-gw \
  --description "Exposes only generate_guess - the only tool the Working Agent's role can reach" \
  --protocol-type MCP --authorizer-type AWS_IAM --json

$AGENTCORE add gateway \
  --name num-guess-inspector-gw \
  --description "Exposes only read_secret - the only tool the Inspector Agent's role can reach" \
  --protocol-type MCP --authorizer-type AWS_IAM --json

# --- Lambdas (3): deploy via boto3 first (non-interactive `add gateway-target`/
# `add evaluator` only accept a pre-existing Lambda ARN, no deploy-from-source
# flag) - writes .deployed_lambdas.json with the 3 ARNs.
python ../deploy_lambdas.py

# --- Gateway targets (2) ---
# Two gotchas discovered by trial and error against the live CLI:
#  1. --outbound-auth is NOT accepted for --type lambda-function-arn (only
#     relevant for mcp-server/http-runtime/open-api-schema targets) - omit it.
#  2. --tool-schema-file must be an ABSOLUTE path to a bare JSON ARRAY of
#     ToolDefinition objects ([{name, description, inputSchema}]), not a
#     relative path and not wrapped in {"tools": [...]}.
GEN_GUESS_ARN=$(python3 -c "import json;print(json.load(open('../.deployed_lambdas.json'))['generate_guess'])")
GEN_GUESS_SCHEMA="$(cd .. && pwd)/tool_schemas/generate_guess.json"
$AGENTCORE add gateway-target \
  --name generate_guess --gateway num-guess-working-gw \
  --type lambda-function-arn \
  --lambda-arn "$GEN_GUESS_ARN" \
  --tool-schema-file "$GEN_GUESS_SCHEMA" \
  --json

READ_SECRET_ARN=$(python3 -c "import json;print(json.load(open('../.deployed_lambdas.json'))['read_secret'])")
READ_SECRET_SCHEMA="$(cd .. && pwd)/tool_schemas/read_secret.json"
$AGENTCORE add gateway-target \
  --name read_secret --gateway num-guess-inspector-gw \
  --type lambda-function-arn \
  --lambda-arn "$READ_SECRET_ARN" \
  --tool-schema-file "$READ_SECRET_SCHEMA" \
  --json

# --- Evaluator (code-based, external Lambda ARN) ---
EVAL_ARN=$(python3 -c "import json;print(json.load(open('../.deployed_lambdas.json'))['no_secret_leak_evaluator'])")
$AGENTCORE add evaluator \
  --name number_guessing_no_secret_leak_evaluator \
  --level TRACE --type code-based \
  --lambda-arn "$EVAL_ARN" \
  --json

# --- Guardrail (plain Bedrock resource, not agentcore.json-managed) ---
python ../post_deploy/create_guardrail.py
# -> hand-set the printed GUARDRAIL_ID/GUARDRAIL_VERSION/GUARDRAIL_BLOCKED_MESSAGE
#    into the Inspector Agent's envVars in agentcore.json (no CLI flag for this).
# Actual values from the first run (regenerate if the guardrail is ever recreated):
#   GUARDRAIL_ID=x635vhd7ug25  GUARDRAIL_VERSION=DRAFT
#   GUARDRAIL_BLOCKED_MESSAGE="Output blocked by the no-secret-leak guardrail."

# --- Static envVars (BEDROCK_MODEL_ID, AWS_REGION, GUARDRAIL_*,
# MAX_LOOPS_PARAM_NAME) + Project tags on every runtime/memory/gateway/
# evaluator - hand-authored (no non-interactive CLI flag exists for envVars
# or tags), see the inline `python3 <<EOF ... EOF` blocks used to patch
# agentcore.json - reproduced in deploy/agentcore-cli/patch_agentcore_json.py.
python3 ../patch_agentcore_json.py
$AGENTCORE validate

# =====================================================================
# PHASE 1 DEPLOY: runtimes + gateways (+ targets) + memory + evaluator,
# with NO cross-resource `connections` yet. IMPORTANT: Gateway/Runtime ARNs
# are not known before creation (unlike the pre-deployed Lambda ARNs above),
# so no runtime can be granted gateway/runtime access yet - every runtime's
# execution role gets ONLY the implicit, all-agents Memory access AgentCore
# grants automatically (confirmed by reading node_modules/@aws/agentcore-cdk's
# AgentCoreApplication.wireMemoriesToAgents - Gateway/Runtime access is never
# implicit, only Memory is). This is a safe, default-deny intermediate state.
# =====================================================================
$AGENTCORE deploy -y

# --- After phase 1, capture the real ARNs this deploy produced ---
# $AGENTCORE status --json > ../.deployed_state.json
# (or `agentcore fetch`) - used to fill in phase 2's connections + envVars below.

# =====================================================================
# PHASE 2: hand-add `connections` (Working->its own gateway only,
# Inspector->its own gateway only, Orchestrator->both runtimes) using the
# real ARNs from phase 1, plus the Orchestrator's WORKING_AGENT_ROLE_ARN /
# INSPECTOR_AGENT_GATEWAY_ARN static envVars (for guard_node's IAM
# introspection check) and an `additionalPolicies` grant letting the
# Orchestrator's role call iam:ListRolePolicies/GetRolePolicy on the
# Working Agent's role ARN specifically. No non-interactive CLI flag exists
# for `connections` - hand-authored against .llm-context/agentcore.ts, then:
# =====================================================================
# $AGENTCORE validate
# $AGENTCORE deploy -y

# --- Deploy everything (first step that is genuinely irreversible/billable
# beyond the Lambdas above - confirm before running) ---
# $AGENTCORE deploy --target default -y

# =====================================================================
# POST-DEPLOY: tag CloudWatch log groups. These are auto-created (by Lambda
# on first invoke, by CodeBuild on first build, by the AgentCore Runtime
# service on first invoke) rather than declared as template resources, so
# they never receive the project-level `tags` CDK applies via Tags.of(stack)
# - confirmed live: every other supporting resource (ECR repos, IAM roles,
# KMS keys, the CodeBuild project, the CFN stack itself) picked up the
# `Project` tag from that cascade, but every log group under the stack had
# zero tags until tagged directly. Safe to re-run after every deploy.
# =====================================================================
# uv run python deploy/agentcore-cli/post_deploy/tag_log_groups.py
