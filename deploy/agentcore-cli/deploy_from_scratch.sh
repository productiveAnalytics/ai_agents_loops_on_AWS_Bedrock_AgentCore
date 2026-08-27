#!/usr/bin/env bash
# Full from-scratch deploy: everything from a clean account state (after
# scripts/teardown.py --yes) to a working, tested game. No file needs a
# manual edit between runs - every ARN is read from state files the CLI or
# our own scripts already produce (.deployed_lambdas.json,
# .deployed_state_phase1.json, .guardrail_state.json).
#
# Usage:
#   AWS_PROFILE=prod8ctive deploy/agentcore-cli/deploy_from_scratch.sh [secret_value] [max_loops]
#
# secret_value/max_loops default to 42/10 - only used for the smoke-test
# invoke at the end; re-seed with scripts/setup_secret.py for a real game.
#
# Requires: uv, node/npx, jq, and AWS credentials for AWS_PROFILE (defaults
# to prod8ctive/us-west-2 - see shared/resource_config.py).
set -euo pipefail

export AWS_PROFILE="${AWS_PROFILE:-prod8ctive}"
export AWS_REGION="${AWS_REGION:-us-west-2}"
SECRET_VALUE="${1:-42}"
MAX_LOOPS="${2:-10}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CLI_DIR="$REPO_ROOT/deploy/agentcore-cli"
PROJECT_DIR="$CLI_DIR/agentcore"
STACK_NAME="AgentCore-agentcorecli-default"

cd "$REPO_ROOT"

echo "==> Resetting agentcore.json to a clean phase-1 baseline"
uv run python "$CLI_DIR/reset_agentcore_json_to_phase1.py"

echo "==> Deploying the 3 Lambdas"
uv run python "$CLI_DIR/deploy_lambdas.py"

echo "==> Creating the Guardrail"
uv run python "$CLI_DIR/post_deploy/create_guardrail.py"

echo "==> Patching phase-1 envVars/tags"
uv run python "$CLI_DIR/patch_agentcore_json.py"

echo "==> Validating (phase 1)"
(cd "$PROJECT_DIR" && npx --yes @aws/agentcore validate)

echo "==> Deploying phase 1 (Runtimes, Gateways, Memory, Evaluator)"
(cd "$CLI_DIR" && npx --yes @aws/agentcore deploy -y)

echo "==> Capturing phase-1 deployed state"
(cd "$CLI_DIR" && npx --yes @aws/agentcore status --json > .deployed_state_phase1.json)

echo "==> Patching phase-2 connections/envVars/additionalPolicies"
uv run python "$CLI_DIR/patch_agentcore_json_phase2.py"

echo "==> Validating (phase 2)"
(cd "$PROJECT_DIR" && npx --yes @aws/agentcore validate)

echo "==> Deploying phase 2 (cross-resource wiring + IAM Deny isolation)"
(cd "$CLI_DIR" && npx --yes @aws/agentcore deploy -y)

echo "==> Backfilling the Project tag onto the stack itself (CDK's Tags.of() doesn't reach the stack-level CFN tags or the CodeBuild project's own role)"
CURRENT_TAGS=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --query 'Stacks[0].Tags' --output json)
MERGED_TAGS=$(echo "$CURRENT_TAGS" | jq '. + [{"Key":"Project","Value":"number_guessing_ai_agents"}] | unique_by(.Key)')
aws cloudformation update-stack \
  --stack-name "$STACK_NAME" \
  --use-previous-template \
  --parameters ParameterKey=BootstrapVersion,UsePreviousValue=true \
  --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM CAPABILITY_AUTO_EXPAND \
  --tags "$MERGED_TAGS" > /dev/null
aws cloudformation wait stack-update-complete --stack-name "$STACK_NAME"

echo "==> Seeding a smoke-test secret (value=$SECRET_VALUE, max_loops=$MAX_LOOPS)"
uv run python scripts/setup_secret.py --secret-value "$SECRET_VALUE" --max-loops "$MAX_LOOPS"

echo "==> Invoking the orchestrator to confirm the deployment actually works"
(cd "$CLI_DIR" && npx --yes @aws/agentcore invoke --runtime number_guessing_orchestrator_agent --prompt start --json)

echo "==> Tagging CloudWatch log groups (the Runtime invoke above just created the 3 per-runtime log groups)"
uv run python "$CLI_DIR/post_deploy/tag_log_groups.py"

echo "==> Done. Re-seed a real secret with scripts/setup_secret.py before playing for real."
