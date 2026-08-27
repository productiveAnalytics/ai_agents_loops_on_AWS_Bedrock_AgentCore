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

# --- Gateway targets (2) - PENDING: run deploy_lambdas.py first, then fill
# in the printed ARNs below. Non-interactive `add gateway-target` only
# accepts a pre-existing Lambda ARN (no deploy-from-source flag), so the
# Lambdas are created directly via boto3 (see deploy_lambdas.py) rather than
# by the CLI.
#
# python deploy_lambdas.py   # writes .deployed_lambdas.json with the 3 ARNs
#
# $AGENTCORE add gateway-target \
#   --name generate_guess --gateway num-guess-working-gw \
#   --type lambda-function-arn \
#   --lambda-arn "$(python3 -c "import json;print(json.load(open('../.deployed_lambdas.json'))['generate_guess'])")" \
#   --tool-schema-file ../tool_schemas/generate_guess.json \
#   --outbound-auth none --json
#
# $AGENTCORE add gateway-target \
#   --name read_secret --gateway num-guess-inspector-gw \
#   --type lambda-function-arn \
#   --lambda-arn "$(python3 -c "import json;print(json.load(open('../.deployed_lambdas.json'))['read_secret'])")" \
#   --tool-schema-file ../tool_schemas/read_secret.json \
#   --outbound-auth none --json

# --- Evaluator (code-based, external Lambda ARN) - PENDING, same reason ---
# $AGENTCORE add evaluator \
#   --name number_guessing_no_secret_leak_evaluator \
#   --level TRACE --type code-based \
#   --lambda-arn "$(python3 -c "import json;print(json.load(open('../.deployed_lambdas.json'))['no_secret_leak_evaluator'])")" \
#   --json

# --- Guardrail (plain Bedrock resource, not agentcore.json-managed) ---
# python post_deploy/create_guardrail.py
# -> hand-set the printed GUARDRAIL_ID/GUARDRAIL_VERSION/GUARDRAIL_BLOCKED_MESSAGE
#    into the Inspector Agent's envVars in agentcore.json (no CLI flag for this).

# --- Connections (runtime -> gateway/memory IAM wiring) and remaining
# envVars (BEDROCK_MODEL_ID, AWS_REGION, MAX_LOOPS_PARAM_NAME, the two
# runtime ARNs for the Orchestrator, etc.) - PENDING: no non-interactive CLI
# flag was found for `connections`; these get hand-authored directly into
# agentcore.json against the confirmed schema in .llm-context/agentcore.ts,
# then checked with:
# $AGENTCORE validate

# --- Deploy everything (first step that is genuinely irreversible/billable
# beyond the Lambdas above - confirm before running) ---
# $AGENTCORE deploy --target default -y
