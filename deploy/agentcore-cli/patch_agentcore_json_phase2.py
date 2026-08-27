"""Phase-2 patch: adds `connections` and the remaining static envVars now
that phase 1's `agentcore deploy` produced real ARNs for the runtimes,
gateways, and memory.

Wiring:
  - Working Agent  -> connection to num-guess-working-gw ONLY (id "gw")
  - Inspector Agent -> connection to num-guess-inspector-gw ONLY (id "gw")
  - Orchestrator   -> connections to both runtimes (ids "working"/"inspector"),
                       plus static WORKING_AGENT_ROLE_ARN/INSPECTOR_AGENT_GATEWAY_ARN
                       envVars and an additionalPolicies grant so guard_node
                       can introspect the Working Agent's role.

Each connection's IAM grant is IDENTITY-based, added directly to the calling
runtime's own execution role (confirmed by reading
@aws/agentcore-cdk's wire-connections.js) - scoped to exactly the one target
ARN in that connection.

IMPORTANT DISCOVERED CONSTRAINT: @aws/agentcore-cdk's AgentCoreMcp.js
(wireGatewayUrlsToAgents) grants `bedrock-agentcore:InvokeGateway` on EVERY
in-project gateway to EVERY in-project runtime UNCONDITIONALLY - its own
source comment says "all resources have implicit access, so all gateways
are wired to all agents." `connections` cannot opt a runtime out of this;
it only adds env vars/permissions, it never restricts the automatic grant.
So Working/Inspector each also get an explicit IAM DENY (via
additionalPolicies -> app/<agent>/additional-policy.json) on the OTHER
agent's gateway ARN - an explicit Deny always overrides any Allow in AWS
IAM, including this automatic one, restoring real isolation despite the
CLI's default-permissive in-project behavior.

All ARNs are read from deploy/agentcore-cli/.deployed_state_phase1.json
(produced by `agentcore status --json`, itself run after phase 1's
`agentcore deploy`) and .guardrail_state.json (from create_guardrail.py) -
never hardcoded - so a from-scratch redeploy (fresh ARNs on every deploy)
never needs a code edit here. The 3 additional-policy.json files are
generated fresh from those same ARNs, not hand-maintained.

Run with:
    AWS_PROFILE=<profile> npx @aws/agentcore status --json > .deployed_state_phase1.json
    uv run python deploy/agentcore-cli/patch_agentcore_json_phase2.py
    cd agentcore && npx @aws/agentcore validate && AWS_PROFILE=<profile> npx @aws/agentcore deploy -y
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from shared.resource_config import (  # noqa: E402
    AWS_REGION,
    INSPECTOR_AGENT_GATEWAY_NAME,
    INSPECTOR_AGENT_RUNTIME_NAME,
    MAX_LOOPS_PARAM,
    WORKING_AGENT_GATEWAY_NAME,
    WORKING_AGENT_RUNTIME_NAME,
)

AGENTCORE_ROOT = Path(__file__).resolve().parent
AGENTCORE_JSON = AGENTCORE_ROOT / "agentcore" / "agentcore.json"
DEPLOYED_STATE_PATH = AGENTCORE_ROOT / ".deployed_state_phase1.json"
GUARDRAIL_STATE_PATH = AGENTCORE_ROOT / ".guardrail_state.json"
REPO_ROOT = AGENTCORE_ROOT.parents[1]

MARKETPLACE_STATEMENT = {
    "Sid": "AllowMarketplaceSubscriptionForBedrockModels",
    "Effect": "Allow",
    "Action": ["aws-marketplace:ViewSubscriptions", "aws-marketplace:Subscribe"],
    "Resource": "*",
}


def _load_deployed_state() -> dict:
    if not DEPLOYED_STATE_PATH.exists():
        raise SystemExit(
            f"{DEPLOYED_STATE_PATH} not found - run phase 1's "
            "`agentcore deploy` first, then:\n"
            f"  AWS_PROFILE=<profile> npx @aws/agentcore status --json > {DEPLOYED_STATE_PATH}"
        )
    return json.loads(DEPLOYED_STATE_PATH.read_text())["deployedState"]["targets"]["default"]["resources"]


def _load_guardrail_arn(account_id: str) -> str:
    if not GUARDRAIL_STATE_PATH.exists():
        raise SystemExit(f"{GUARDRAIL_STATE_PATH} not found - run post_deploy/create_guardrail.py first.")
    guardrail_id = json.loads(GUARDRAIL_STATE_PATH.read_text())["guardrailId"]
    return f"arn:aws:bedrock:{AWS_REGION}:{account_id}:guardrail/{guardrail_id}"


def _write_policy(app_dir: str, statements: list[dict]) -> None:
    path = REPO_ROOT / "app" / app_dir / "additional-policy.json"
    path.write_text(json.dumps({"Version": "2012-10-17", "Statement": statements}, indent=2) + "\n")
    print(f"Wrote {path}")


def main() -> None:
    resources = _load_deployed_state()
    runtimes = resources["runtimes"]
    gateways = resources["mcp"]["gateways"]

    working_runtime_arn = runtimes[WORKING_AGENT_RUNTIME_NAME]["runtimeArn"]
    inspector_runtime_arn = runtimes[INSPECTOR_AGENT_RUNTIME_NAME]["runtimeArn"]
    working_role_arn = runtimes[WORKING_AGENT_RUNTIME_NAME]["roleArn"]
    working_gateway_arn = gateways[WORKING_AGENT_GATEWAY_NAME]["gatewayArn"]
    inspector_gateway_arn = gateways[INSPECTOR_AGENT_GATEWAY_NAME]["gatewayArn"]

    account_id = working_runtime_arn.split(":")[4]
    guardrail_arn = _load_guardrail_arn(account_id)
    max_loops_param_arn = f"arn:aws:ssm:{AWS_REGION}:{account_id}:parameter{MAX_LOOPS_PARAM}"

    data = json.loads(AGENTCORE_JSON.read_text())
    for runtime in data["runtimes"]:
        if runtime["name"] == "number_guessing_working_agent":
            runtime["connections"] = [{"id": "gw", "to": {"type": "gateway", "arn": working_gateway_arn}}]
            runtime["additionalPolicies"] = ["additional-policy.json"]
        elif runtime["name"] == "number_guessing_inspector_agent":
            runtime["connections"] = [{"id": "gw", "to": {"type": "gateway", "arn": inspector_gateway_arn}}]
            runtime["additionalPolicies"] = ["additional-policy.json"]
        elif runtime["name"] == "number_guessing_orchestrator_agent":
            runtime["connections"] = [
                {"id": "working", "to": {"type": "runtime", "arn": working_runtime_arn}},
                {"id": "inspector", "to": {"type": "runtime", "arn": inspector_runtime_arn}},
            ]
            existing_env = {e["name"]: e["value"] for e in runtime.get("envVars", [])}
            existing_env["WORKING_AGENT_ROLE_ARN"] = working_role_arn
            existing_env["INSPECTOR_AGENT_GATEWAY_ARN"] = inspector_gateway_arn
            runtime["envVars"] = [{"name": k, "value": v} for k, v in existing_env.items()]
            runtime["additionalPolicies"] = ["additional-policy.json"]
    AGENTCORE_JSON.write_text(json.dumps(data, indent=2) + "\n")
    print(f"Patched connections/envVars/additionalPolicies in {AGENTCORE_JSON}")

    _write_policy("working_agent", [
        {
            "Sid": "DenyInvokingInspectorGateway",
            "Effect": "Deny",
            "Action": "bedrock-agentcore:InvokeGateway",
            "Resource": inspector_gateway_arn,
        },
        MARKETPLACE_STATEMENT,
    ])
    _write_policy("inspector_agent", [
        {
            "Sid": "DenyInvokingWorkingGateway",
            "Effect": "Deny",
            "Action": "bedrock-agentcore:InvokeGateway",
            "Resource": working_gateway_arn,
        },
        {
            "Sid": "AllowApplyingNoSecretLeakGuardrail",
            "Effect": "Allow",
            "Action": "bedrock:ApplyGuardrail",
            "Resource": guardrail_arn,
        },
        MARKETPLACE_STATEMENT,
    ])
    _write_policy("orchestrator_agent", [
        {
            "Sid": "AllowIntrospectingWorkingAgentRoleForCheatCheck",
            "Effect": "Allow",
            "Action": ["iam:ListRolePolicies", "iam:GetRolePolicy"],
            "Resource": working_role_arn,
        },
        {
            "Sid": "AllowReadingMaxLoopsParam",
            "Effect": "Allow",
            "Action": "ssm:GetParameter",
            "Resource": max_loops_param_arn,
        },
        {
            "Sid": "AllowInvokingRuntimeEndpoints",
            "Effect": "Allow",
            "Action": ["bedrock-agentcore:InvokeAgentRuntime", "bedrock-agentcore:InvokeAgentRuntimeForUser"],
            "Resource": [
                f"{working_runtime_arn}/runtime-endpoint/*",
                f"{inspector_runtime_arn}/runtime-endpoint/*",
            ],
        },
    ])


if __name__ == "__main__":
    main()
