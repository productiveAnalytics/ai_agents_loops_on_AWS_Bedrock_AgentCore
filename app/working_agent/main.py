"""Working Agent - AgentCore Runtime entrypoint.

Ported from the original project's agents/working_agent.py: same narrowing
algorithm, same HintInterpretation structured-output call. What changed:
- ChatAnthropic -> init_chat_model(..., model_provider="bedrock_converse")
- the local @tool generate_guess -> an MCP tool loaded from this agent's own
  Gateway (working-agent-gateway). This agent's IAM role has no permission on
  inspector-agent-gateway, so it structurally cannot reach read_secret.
- the turn is also written to AgentCore Memory (short-term).
"""

import json
from typing import Literal

from bedrock_agentcore.memory import MemorySessionManager
from bedrock_agentcore.memory.constants import ConversationalMessage, MessageRole
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_mcp_adapters.tools import load_mcp_tools
from mcp import ClientSession
from mcp_proxy_for_aws.client import aws_iam_streamablehttp_client
from pydantic import BaseModel, Field

import config

app = BedrockAgentCoreApp()

_SYSTEM_PROMPT = """You are the Working Agent in a number-guessing game. You cannot see
the secret number. You only get a playful, child-language hint sentence describing how
close your last guess was and whether the secret is higher or lower.
Interpret the hint sentence and extract two things:
- direction: "higher" or "lower" (is the secret above or below the last guess?)
- intensity: "very_close", "close", "far", or "very_far" (how near was the guess?)
Base this purely on the tone and metaphor of the sentence."""


class HintInterpretation(BaseModel):
    direction: Literal["higher", "lower"] = Field(
        description="Whether the secret number is higher or lower than the last guess"
    )
    intensity: Literal["very_close", "close", "far", "very_far"] = Field(
        description="How close the last guess was to the secret"
    )


def _build_llm():
    return init_chat_model(config.BEDROCK_MODEL_ID, model_provider="bedrock_converse", temperature=0)


async def _interpret_hint(hint: str) -> HintInterpretation:
    llm = _build_llm().with_structured_output(HintInterpretation)
    result = await llm.ainvoke(
        [SystemMessage(content=_SYSTEM_PROMPT), HumanMessage(content=hint)]
    )
    assert isinstance(result, HintInterpretation)
    return result


def _narrow_bounds(low: int, high: int, guess: int, hint: HintInterpretation) -> tuple[int, int]:
    if hint.direction == "higher":
        low = max(low, guess + 1)
        if hint.intensity == "very_close":
            high = min(high, guess + 3)
    else:
        high = min(high, guess - 1)
        if hint.intensity == "very_close":
            low = max(low, guess - 3)

    if low > high:
        low, high = config.MIN_GUESS, config.MAX_GUESS
    return low, high


def _parse_guess(tool_result) -> int:
    """MCP tool results come back as a list of content blocks
    ([{"type": "text", "text": "{\"guess\": N}"}]) - confirmed live against
    the deployed Gateway. Also handles a bare dict/JSON string defensively."""
    if isinstance(tool_result, dict):
        if "guess" in tool_result:
            return int(tool_result["guess"])
        if "text" in tool_result:
            return _parse_guess(tool_result["text"])
        raise ValueError(f"Unexpected generate_guess tool result dict shape: {tool_result!r}")
    if isinstance(tool_result, str):
        return int(json.loads(tool_result)["guess"])
    if isinstance(tool_result, list) and tool_result:
        return _parse_guess(tool_result[0])
    raise ValueError(f"Unexpected generate_guess tool result shape: {tool_result!r}")


async def _draw_guess(low: int, high: int) -> int:
    async with aws_iam_streamablehttp_client(
        endpoint=config.GATEWAY_URL,
        aws_region=config.AWS_REGION,
        aws_service="bedrock-agentcore",
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await load_mcp_tools(session)
            # Gateway namespaces MCP tool names as "${target_name}___${tool_name}"
            # (confirmed in AWS docs) - our Gateway target is named "generate-guess".
            generate_guess_tool = next(t for t in tools if t.name == "generate-guess___generate_guess")
            result = await generate_guess_tool.ainvoke({"low": low, "high": high})
            return _parse_guess(result)


def _write_memory_turn(session_id: str, text: str) -> None:
    session_manager = MemorySessionManager(memory_id=config.MEMORY_ID, region_name=config.AWS_REGION)
    memory_session = session_manager.create_memory_session(
        actor_id=config.MEMORY_ACTOR_ID, session_id=session_id
    )
    memory_session.add_turns(messages=[ConversationalMessage(text, MessageRole.ASSISTANT)])


@app.entrypoint
async def agent_invocation(payload: dict, context) -> dict:
    """payload: {low, high, last_guess, last_feedback, session_id}
    returns: {low, high, guess} - the narrowed bounds and the next guess.
    Never reads the secret - only ever calls the generate_guess Gateway tool
    for randomness."""
    low = int(payload["low"])
    high = int(payload["high"])
    last_guess = payload.get("last_guess")
    last_feedback = payload.get("last_feedback")
    session_id = payload["session_id"]

    if last_guess is not None and last_feedback:
        hint = await _interpret_hint(last_feedback)
        low, high = _narrow_bounds(low, high, int(last_guess), hint)

    guess = await _draw_guess(low, high)
    _write_memory_turn(session_id, f"Guessed {guess} (search range was [{low}, {high}]).")

    return {"low": low, "high": high, "guess": guess}


if __name__ == "__main__":
    app.run()
