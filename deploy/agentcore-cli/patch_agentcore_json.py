"""Hand-patches agentcore.json with the static envVars and Project tags that
have no non-interactive `agentcore add ...` CLI flag. Idempotent - re-running
just overwrites the same envVars/tags.

Phase-1 only: does NOT add `connections` (those need real Gateway/Runtime
ARNs that don't exist until after the first `agentcore deploy`). See
bootstrap_commands.sh's PHASE 2 section for that follow-up edit.

Run with: uv run python deploy/agentcore-cli/patch_agentcore_json.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from shared.resource_config import AWS_REGION, BEDROCK_MODEL_ID, MAX_LOOPS_PARAM, PROJECT_TAG  # noqa: E402

AGENTCORE_JSON = Path(__file__).resolve().parent / "agentcore" / "agentcore.json"

# GUARDRAIL_* values come from post_deploy/create_guardrail.py's printed output.
GUARDRAIL_ID = "x635vhd7ug25"
GUARDRAIL_VERSION = "DRAFT"
GUARDRAIL_BLOCKED_MESSAGE = "Output blocked by the no-secret-leak guardrail."

ENV_BY_RUNTIME = {
    "number_guessing_working_agent": {
        "BEDROCK_MODEL_ID": BEDROCK_MODEL_ID,
        "AWS_REGION": AWS_REGION,
    },
    "number_guessing_inspector_agent": {
        "BEDROCK_MODEL_ID": BEDROCK_MODEL_ID,
        "AWS_REGION": AWS_REGION,
        "GUARDRAIL_ID": GUARDRAIL_ID,
        "GUARDRAIL_VERSION": GUARDRAIL_VERSION,
        "GUARDRAIL_BLOCKED_MESSAGE": GUARDRAIL_BLOCKED_MESSAGE,
    },
    "number_guessing_orchestrator_agent": {
        "AWS_REGION": AWS_REGION,
        "MAX_LOOPS_PARAM_NAME": MAX_LOOPS_PARAM,
    },
}


def main() -> None:
    data = json.loads(AGENTCORE_JSON.read_text())

    for runtime in data["runtimes"]:
        env = ENV_BY_RUNTIME.get(runtime["name"])
        if env:
            runtime["envVars"] = [{"name": k, "value": v} for k, v in env.items()]
        runtime["tags"] = PROJECT_TAG

    for memory in data["memories"]:
        memory["tags"] = PROJECT_TAG
    for gateway in data["agentCoreGateways"]:
        gateway["tags"] = PROJECT_TAG
    for evaluator in data["evaluators"]:
        evaluator["tags"] = PROJECT_TAG

    AGENTCORE_JSON.write_text(json.dumps(data, indent=2) + "\n")
    print(f"Patched envVars/tags in {AGENTCORE_JSON}")


if __name__ == "__main__":
    main()
