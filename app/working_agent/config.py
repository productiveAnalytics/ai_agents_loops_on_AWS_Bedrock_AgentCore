"""Working Agent runtime config - read from env vars injected at deploy time.

Kept deliberately free of any import from shared/ so this agent ships as a
self-contained container image; the *values* still originate from
shared/resource_config.py via the deploy scripts that set these env vars.
"""

import os

BEDROCK_MODEL_ID = os.environ["BEDROCK_MODEL_ID"]
GATEWAY_URL = os.environ["GATEWAY_URL"]  # working-agent-gateway's MCP endpoint - the only gateway this agent can reach
AWS_REGION = os.environ["AWS_REGION"]
MEMORY_ID = os.environ["MEMORY_ID"]
MEMORY_ACTOR_ID = os.environ.get("MEMORY_ACTOR_ID", "working-agent")

MIN_GUESS = int(os.environ.get("MIN_GUESS", "1"))
MAX_GUESS = int(os.environ.get("MAX_GUESS", "100"))
