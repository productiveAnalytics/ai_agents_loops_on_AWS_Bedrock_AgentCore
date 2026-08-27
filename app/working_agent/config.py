"""Working Agent runtime config - read from env vars injected at deploy time.

Kept deliberately free of any import from shared/ so this agent ships as a
self-contained container image; the *values* still originate from
shared/resource_config.py via the deploy scripts that set these env vars.
"""

import os

BEDROCK_MODEL_ID = os.environ["BEDROCK_MODEL_ID"]
# Auto-injected by the agentcore CLI's `connections` wiring (gateway connection
# id "gw" on this runtime) - working-agent-gateway's MCP endpoint, the only
# gateway this agent's role can reach.
GATEWAY_URL = os.environ["GATEWAY_GW_URL"]
AWS_REGION = os.environ["AWS_REGION"]
# Auto-injected for every runtime in the project (memory access is implicit,
# per-project, for all agents - see AgentCoreApplication.wireMemoriesToAgents).
MEMORY_ID = os.environ["MEMORY_NUMBER_GUESSING_GAME_MEMORY_ID"]
MEMORY_ACTOR_ID = os.environ.get("MEMORY_ACTOR_ID", "working-agent")

MIN_GUESS = int(os.environ.get("MIN_GUESS", "1"))
MAX_GUESS = int(os.environ.get("MAX_GUESS", "100"))
