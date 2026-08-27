"""Inspector Agent runtime config - read from env vars injected at deploy time."""

import os

BEDROCK_MODEL_ID = os.environ["BEDROCK_MODEL_ID"]
GATEWAY_URL = os.environ["GATEWAY_URL"]  # inspector-agent-gateway's MCP endpoint - the only gateway this agent can reach
AWS_REGION = os.environ["AWS_REGION"]
MEMORY_ID = os.environ["MEMORY_ID"]
MEMORY_ACTOR_ID = os.environ.get("MEMORY_ACTOR_ID", "inspector-agent")

GUARDRAIL_ID = os.environ["GUARDRAIL_ID"]
GUARDRAIL_VERSION = os.environ.get("GUARDRAIL_VERSION", "DRAFT")
# Must match shared/guardrail_config.py's blocked_outputs_messaging exactly -
# Bedrock replaces the model's output with this string verbatim when the
# guardrail's BLOCK action fires, so it doubles as the intervention signal.
GUARDRAIL_BLOCKED_MESSAGE = os.environ["GUARDRAIL_BLOCKED_MESSAGE"]

MIN_GUESS = int(os.environ.get("MIN_GUESS", "1"))
MAX_GUESS = int(os.environ.get("MAX_GUESS", "100"))
