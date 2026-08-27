"""Inspector Agent - AgentCore Runtime entrypoint.

Ported from the original project's agents/inspector_agent.py: check_guess's
comparison logic is untouched. What changed:
- ChatAnthropic -> a Bedrock Converse chat model with a Guardrail attached
  natively via guardrail_config, so the "never leak the secret" instruction
  is backed by an AWS-enforced digit filter, not just a system prompt.
- the local @tool read_secret -> an MCP tool loaded from this agent's own
  Gateway (inspector-agent-gateway). The Working Agent's role has no
  permission on this gateway at all.
- the turn is also written to AgentCore Memory (short-term).
"""

import json

from bedrock_agentcore.memory import MemorySessionManager
from bedrock_agentcore.memory.constants import ConversationalMessage, MessageRole
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_mcp_adapters.tools import load_mcp_tools
from mcp import ClientSession
from mcp_proxy_for_aws.client import aws_iam_streamablehttp_client

import config

app = BedrockAgentCoreApp()

_SYSTEM_PROMPT = """You are a playful Inspector in a number-guessing game for children.
You know the secret number and have just compared it to a guess.
Given the guess, the direction (whether the secret is higher or lower than the guess),
and the intensity (how close the guess is), write exactly ONE short, fun, child-language
sentence that hints at the direction and distance using a playful metaphor.
Never reveal the secret number or the guess itself. Never use any digit characters -
be creative (trains, animals, weather, temperature, distance, speed, etc).
Respond with only the sentence, nothing else."""

_FALLBACK_HINT = "The wind is whispering something, but it's too shy to say which way!"


def _build_llm():
    return init_chat_model(
        config.BEDROCK_MODEL_ID,
        model_provider="bedrock_converse",
        temperature=0.9,
        guardrail_config={
            "guardrailIdentifier": config.GUARDRAIL_ID,
            "guardrailVersion": config.GUARDRAIL_VERSION,
            "trace": "enabled",
        },
    )


def check_guess(guess: int, secret: int) -> dict:
    """Compare a guess to the secret. Ported verbatim from the original project."""
    if guess == secret:
        return {"exact": True}

    distance = abs(guess - secret)
    span = config.MAX_GUESS - config.MIN_GUESS
    if distance <= span * 0.03:
        intensity = "very_close"
    elif distance <= span * 0.10:
        intensity = "close"
    elif distance <= span * 0.30:
        intensity = "far"
    else:
        intensity = "very_far"

    direction = "higher" if secret > guess else "lower"
    return {"exact": False, "direction": direction, "intensity": intensity}


def _parse_secret(tool_result) -> int:
    if isinstance(tool_result, dict):
        return int(tool_result["secret_value"])
    if isinstance(tool_result, str):
        return int(json.loads(tool_result)["secret_value"])
    if isinstance(tool_result, list) and tool_result:
        return _parse_secret(tool_result[0])
    raise ValueError(f"Unexpected read_secret tool result shape: {tool_result!r}")


async def _read_secret() -> int:
    async with aws_iam_streamablehttp_client(
        endpoint=config.GATEWAY_URL,
        aws_region=config.AWS_REGION,
        aws_service="bedrock-agentcore",
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await load_mcp_tools(session)
            read_secret_tool = next(t for t in tools if t.name == "read_secret")
            result = await read_secret_tool.ainvoke({})
            return _parse_secret(result)


async def _generate_hint(direction: str, intensity: str) -> str:
    llm = _build_llm()
    user_prompt = (
        f"Direction: the secret is {direction} than the guess.\n"
        f"Intensity: {intensity}.\n"
        "Write the hint sentence now."
    )
    response = await llm.ainvoke(
        [SystemMessage(content=_SYSTEM_PROMPT), HumanMessage(content=user_prompt)]
    )
    text = str(response.content).strip()
    if text == config.GUARDRAIL_BLOCKED_MESSAGE:
        # The guardrail's digit filter fired - fall back to a canned safe hint
        # rather than surface the blocked-output message to the player.
        return _FALLBACK_HINT
    return text


def _write_memory_turn(session_id: str, text: str) -> None:
    session_manager = MemorySessionManager(memory_id=config.MEMORY_ID, region_name=config.AWS_REGION)
    memory_session = session_manager.create_memory_session(
        actor_id=config.MEMORY_ACTOR_ID, session_id=session_id
    )
    memory_session.add_turns(messages=[ConversationalMessage(text, MessageRole.ASSISTANT)])


@app.entrypoint
async def agent_invocation(payload: dict, context) -> dict:
    """payload: {guess, session_id}
    returns: {feedback} - "YaHoo!" on exact match, else a guardrail-checked hint."""
    guess = int(payload["guess"])
    session_id = payload["session_id"]

    secret = await _read_secret()
    result = check_guess(guess, secret)

    if result["exact"]:
        feedback = "YaHoo!"
    else:
        feedback = await _generate_hint(result["direction"], result["intensity"])

    _write_memory_turn(session_id, feedback)
    return {"feedback": feedback}


if __name__ == "__main__":
    app.run()
