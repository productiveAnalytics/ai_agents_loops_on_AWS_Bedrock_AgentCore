"""Create the Bedrock Guardrail that blocks any digit in the Inspector's
output. Bedrock Guardrails are a plain `bedrock` service resource - they are
not part of agentcore.json's schema, so this is a standalone boto3 step (not
something `agentcore deploy` provisions).

Run with: uv run python deploy/agentcore-cli/post_deploy/create_guardrail.py
Idempotent: looks up an existing guardrail by name before creating.

After running, put the printed guardrail ID/version into the Inspector
Agent's envVars in agentcore.json (GUARDRAIL_ID, GUARDRAIL_VERSION,
GUARDRAIL_BLOCKED_MESSAGE) before `agentcore deploy`.
"""

import json
import sys
from pathlib import Path

import boto3

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from shared.guardrail_config import build_guardrail_config  # noqa: E402
from shared.resource_config import AWS_PROFILE, AWS_REGION, GUARDRAIL_NAME  # noqa: E402


def main() -> None:
    session = boto3.Session(profile_name=AWS_PROFILE, region_name=AWS_REGION)
    bedrock = session.client("bedrock")

    # guardrailIdentifier filters by ID/ARN, not name - list unfiltered and match by name.
    all_guardrails = bedrock.list_guardrails().get("guardrails", [])
    existing = [g for g in all_guardrails if g["name"] == GUARDRAIL_NAME]
    if existing:
        guardrail_id = existing[0]["id"]
        version = existing[0].get("version", "DRAFT")
        print(f"Guardrail already exists: id={guardrail_id} version={version}")
    else:
        config = build_guardrail_config()
        response = bedrock.create_guardrail(**config)
        guardrail_id = response["guardrailId"]
        version = response.get("version", "DRAFT")
        print(f"Created guardrail: id={guardrail_id} version={version}")

    blocked_message = build_guardrail_config()["blockedOutputsMessaging"]
    print()
    print("Set these in the Inspector Agent's agentcore.json envVars:")
    print(json.dumps({
        "GUARDRAIL_ID": guardrail_id,
        "GUARDRAIL_VERSION": version,
        "GUARDRAIL_BLOCKED_MESSAGE": blocked_message,
    }, indent=2))


if __name__ == "__main__":
    main()
