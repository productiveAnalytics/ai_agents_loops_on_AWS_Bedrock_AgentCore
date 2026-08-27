"""Gateway Lambda target for the `generate_guess` tool.

Bound only to working-agent-gateway - this is the only randomness source the
Working Agent's Runtime role can ever reach (it has no InvokeGateway
permission on inspector-agent-gateway, so it structurally cannot read the
secret regardless of what its LLM attempts).

Gateway Lambda target contract: `event` is a flat dict of the tool's
inputSchema properties; the return value is the tool's JSON result.
"""

import random


def handler(event: dict, context) -> dict:
    low = int(event["low"])
    high = int(event["high"])
    return {"guess": random.randint(low, high)}
