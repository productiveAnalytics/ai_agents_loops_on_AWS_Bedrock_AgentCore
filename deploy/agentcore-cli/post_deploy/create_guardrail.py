"""Create the Bedrock Guardrail that blocks any digit in the Inspector's
output. Bedrock Guardrails are a plain `bedrock` service resource - they are
not part of agentcore.json's schema, so this is a standalone boto3 step (not
something `agentcore deploy` provisions).

Run with: uv run python deploy/agentcore-cli/post_deploy/create_guardrail.py
Idempotent: looks up an existing guardrail by name before creating.

Writes the guardrail id/version/blocked-message to
deploy/agentcore-cli/.guardrail_state.json (not committed - regenerate by
re-running this script) for patch_agentcore_json.py to read - no ARN/ID
ever needs to be hand-copied into a script.
"""

import json
import sys
from pathlib import Path

import boto3

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from shared.guardrail_config import build_guardrail_config  # noqa: E402
from shared.resource_config import AWS_PROFILE, AWS_REGION, GUARDRAIL_NAME  # noqa: E402

STATE_PATH = Path(__file__).resolve().parents[1] / ".guardrail_state.json"


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
    STATE_PATH.write_text(json.dumps({
        "guardrailId": guardrail_id,
        "guardrailVersion": version,
        "guardrailBlockedMessage": blocked_message,
    }, indent=2) + "\n")
    print(f"Wrote {STATE_PATH}")


if __name__ == "__main__":
    main()
