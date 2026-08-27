"""Strips phase-2 wiring out of agentcore.json so a from-scratch redeploy
(after a full `scripts/teardown.py --yes`) can start clean, instead of
carrying `connections`/`additionalPolicies` and envVars that reference
ARNs from resources that no longer exist.

Removes, from every runtime: `connections`, `additionalPolicies`, and the
specific envVars phase 2 injects (GUARDRAIL_ID/GUARDRAIL_VERSION/
GUARDRAIL_BLOCKED_MESSAGE on the Inspector, WORKING_AGENT_ROLE_ARN/
INSPECTOR_AGENT_GATEWAY_ARN on the Orchestrator) - everything else
(BEDROCK_MODEL_ID, AWS_REGION, MAX_LOOPS_PARAM_NAME, tags) is untouched
since patch_agentcore_json.py (phase 1) manages those directly.

Run with: uv run python deploy/agentcore-cli/reset_agentcore_json_to_phase1.py
Then re-run the normal phase 1 -> phase 2 sequence - or just run
deploy/agentcore-cli/deploy_from_scratch.sh, which calls this first.
"""

import json
from pathlib import Path

AGENTCORE_JSON = Path(__file__).resolve().parent / "agentcore" / "agentcore.json"

PHASE2_ENV_VAR_NAMES = {
    "GUARDRAIL_ID",
    "GUARDRAIL_VERSION",
    "GUARDRAIL_BLOCKED_MESSAGE",
    "WORKING_AGENT_ROLE_ARN",
    "INSPECTOR_AGENT_GATEWAY_ARN",
}


def main() -> None:
    data = json.loads(AGENTCORE_JSON.read_text())

    for runtime in data["runtimes"]:
        runtime.pop("connections", None)
        runtime.pop("additionalPolicies", None)
        runtime["envVars"] = [
            e for e in runtime.get("envVars", []) if e["name"] not in PHASE2_ENV_VAR_NAMES
        ]

    AGENTCORE_JSON.write_text(json.dumps(data, indent=2) + "\n")
    print(f"Reset {AGENTCORE_JSON} to a phase-1 baseline (no connections/additionalPolicies).")


if __name__ == "__main__":
    main()
