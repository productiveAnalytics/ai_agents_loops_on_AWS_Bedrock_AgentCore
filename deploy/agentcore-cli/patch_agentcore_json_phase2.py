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

Run with: uv run python deploy/agentcore-cli/patch_agentcore_json_phase2.py
Then: cd agentcore && npx @aws/agentcore validate && AWS_PROFILE=prod8ctive npx @aws/agentcore deploy -y
"""

import json
from pathlib import Path

AGENTCORE_JSON = Path(__file__).resolve().parent / "agentcore" / "agentcore.json"

# From `agentcore status --json` after phase 1 (see deploy/agentcore-cli/.deployed_state_phase1.json).
WORKING_RUNTIME_ARN = "arn:aws:bedrock-agentcore:us-west-2:778858743114:runtime/agentcorecli_number_guessing_working_agent-H7NdMi9O8W"
INSPECTOR_RUNTIME_ARN = "arn:aws:bedrock-agentcore:us-west-2:778858743114:runtime/agentcorecli_number_guessing_inspector_agent-nlJ6dJ4TXy"
WORKING_ROLE_ARN = "arn:aws:iam::778858743114:role/AgentCore-agentcorecli-de-ApplicationAgentNumberGue-MD7QJrhaMZt7"
WORKING_GATEWAY_ARN = "arn:aws:bedrock-agentcore:us-west-2:778858743114:gateway/agentcorecli-num-guess-working-gw-t8wrhrztlx"
INSPECTOR_GATEWAY_ARN = "arn:aws:bedrock-agentcore:us-west-2:778858743114:gateway/agentcorecli-num-guess-inspector-gw-ek6hd9s87a"


def main() -> None:
    data = json.loads(AGENTCORE_JSON.read_text())

    for runtime in data["runtimes"]:
        if runtime["name"] == "number_guessing_working_agent":
            runtime["connections"] = [
                {"id": "gw", "to": {"type": "gateway", "arn": WORKING_GATEWAY_ARN}}
            ]
            runtime["additionalPolicies"] = ["additional-policy.json"]  # explicit Deny on Inspector's gateway
        elif runtime["name"] == "number_guessing_inspector_agent":
            runtime["connections"] = [
                {"id": "gw", "to": {"type": "gateway", "arn": INSPECTOR_GATEWAY_ARN}}
            ]
            runtime["additionalPolicies"] = ["additional-policy.json"]  # explicit Deny on Working's gateway
        elif runtime["name"] == "number_guessing_orchestrator_agent":
            runtime["connections"] = [
                {"id": "working", "to": {"type": "runtime", "arn": WORKING_RUNTIME_ARN}},
                {"id": "inspector", "to": {"type": "runtime", "arn": INSPECTOR_RUNTIME_ARN}},
            ]
            existing_env = {e["name"]: e["value"] for e in runtime.get("envVars", [])}
            existing_env["WORKING_AGENT_ROLE_ARN"] = WORKING_ROLE_ARN
            existing_env["INSPECTOR_AGENT_GATEWAY_ARN"] = INSPECTOR_GATEWAY_ARN
            runtime["envVars"] = [{"name": k, "value": v} for k, v in existing_env.items()]
            runtime["additionalPolicies"] = ["additional-policy.json"]

    AGENTCORE_JSON.write_text(json.dumps(data, indent=2) + "\n")
    print(f"Patched connections/envVars/additionalPolicies in {AGENTCORE_JSON}")


if __name__ == "__main__":
    main()
