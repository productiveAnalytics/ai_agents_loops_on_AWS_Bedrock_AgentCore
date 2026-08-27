"""The Guardrail that enforces "the Inspector's hint must never contain a
digit" - a fully static rule that guarantees no leak regardless of what the
secret actually is, since child-language hints never legitimately need
digits. Returned as a plain dict of boto3 `create_guardrail` kwargs (the
`bedrock` client expects camelCase parameter names, confirmed against the
live botocore service model/API errors - not xform_name'd snake_case like
most other services) so both the AgentCore CLI deploy path and, later, a CDK
custom resource pass the exact same request body.
"""

from shared.resource_config import GUARDRAIL_NAME, PROJECT_TAG

NO_DIGITS_REGEX_NAME = "no-digits-in-output"


def build_guardrail_config() -> dict:
    """Return kwargs for `bedrock_client.create_guardrail(**build_guardrail_config())`."""
    return {
        "name": GUARDRAIL_NAME,
        "description": (
            "Blocks any digit character in model OUTPUT so the Inspector Agent's "
            "child-language hints can never leak the secret number, regardless of "
            "its value."
        ),
        "sensitiveInformationPolicyConfig": {
            "regexesConfig": [
                {
                    "name": NO_DIGITS_REGEX_NAME,
                    "description": "Matches any digit 0-9; hints are metaphors and never need one.",
                    "pattern": r"\d",
                    "action": "BLOCK",
                    "inputAction": "NONE",
                    "inputEnabled": False,
                    "outputAction": "BLOCK",
                    "outputEnabled": True,
                }
            ]
        },
        "blockedInputMessaging": "Input blocked by the no-secret-leak guardrail.",
        "blockedOutputsMessaging": "Output blocked by the no-secret-leak guardrail.",
        "tags": [{"key": k, "value": v} for k, v in PROJECT_TAG.items()],
    }
