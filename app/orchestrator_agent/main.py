"""Orchestrator Agent - AgentCore Runtime entrypoint. The `agentcore invoke`
front door.

Ported from the original project's agents/orchestrator.py: same LangGraph
StateGraph topology (init -> working -> guard -> inspector -> loop/end),
same state shape, same routing logic. What changed:
- working_node/inspector_node now call InvokeAgentRuntime against the
  Working/Inspector Runtime agents instead of local Python function calls,
  since those are now separate deployables.
- guard_node is a real AWS-verifiable check (GetResourcePolicy) instead of a
  local Python tool-list assertion.
- max_loops comes from SSM instead of agent.properties.
- logging is structured stdout (captured by CloudWatch via Runtime) instead
  of a local logs/*.jsonl file, since the Runtime filesystem is ephemeral.
"""

import json
import time
import uuid
from typing import TypedDict

import boto3
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from langgraph.graph import END, StateGraph

import config

app = BedrockAgentCoreApp()

_ssm = boto3.client("ssm", region_name=config.AWS_REGION)
_agentcore_data = boto3.client("bedrock-agentcore", region_name=config.AWS_REGION)
_agentcore_control = boto3.client("bedrock-agentcore-control", region_name=config.AWS_REGION)

# Tool names the Working Agent must never be able to reach. FORBIDDEN here for
# parity with the original's FORBIDDEN_TOOL_NAMES concept, but the guard now
# checks IAM policy, not a local tool list.
FORBIDDEN_TOOL_NAMES = {"read_secret", "check_guess"}


class HistoryEntry(TypedDict):
    iteration: int
    agent: str
    guess: int | None
    feedback: str | None


class GameState(TypedDict):
    session_id: str
    low: int
    high: int
    guess: int | None
    feedback: str | None
    solved: bool
    loop_count: int
    max_loops: int
    history: list[HistoryEntry]
    cheat_detected: bool


def _log(**fields) -> None:
    print(json.dumps({"timestamp": time.time(), **fields}))


def _invoke_runtime(runtime_arn: str, session_id: str, payload: dict) -> dict:
    response = _agentcore_data.invoke_agent_runtime(
        agentRuntimeArn=runtime_arn,
        runtimeSessionId=session_id,
        payload=json.dumps(payload).encode("utf-8"),
        contentType="application/json",
        accept="application/json",
    )
    body = response["response"].read()
    return json.loads(body)


def init_node(state: GameState) -> GameState:
    max_loops = int(_ssm.get_parameter(Name=config.MAX_LOOPS_PARAM_NAME)["Parameter"]["Value"])
    _log(agent="orchestrator", event="init", max_loops=max_loops, session_id=state["session_id"])
    return {
        "low": config.MIN_GUESS,
        "high": config.MAX_GUESS,
        "guess": None,
        "feedback": None,
        "solved": False,
        "loop_count": 0,
        "max_loops": max_loops,
        "history": [],
        "cheat_detected": False,
    }


def working_node(state: GameState) -> GameState:
    result = _invoke_runtime(
        config.WORKING_AGENT_RUNTIME_ARN,
        state["session_id"],
        {
            "low": state["low"],
            "high": state["high"],
            "last_guess": state["guess"],
            "last_feedback": state["feedback"],
            "session_id": state["session_id"],
        },
    )
    loop_count = state["loop_count"] + 1
    _log(
        agent="working",
        event="guess",
        iteration=loop_count,
        input={"low": state["low"], "high": state["high"], "last_feedback": state["feedback"]},
        output=result,
    )
    return {"low": result["low"], "high": result["high"], "guess": result["guess"], "loop_count": loop_count}


def guard_node(state: GameState) -> GameState:
    """Real AWS-verifiable cheat-boundary check: confirm the Working Agent's
    role is not a named principal on the Inspector's gateway resource policy."""
    try:
        policy_response = _agentcore_control.get_resource_policy(
            resourceArn=config.INSPECTOR_AGENT_GATEWAY_ARN
        )
        policy = json.loads(policy_response["policy"])
        principals = set()
        for statement in policy.get("Statement", []):
            aws_principal = statement.get("Principal", {}).get("AWS")
            if isinstance(aws_principal, str):
                principals.add(aws_principal)
            elif isinstance(aws_principal, list):
                principals.update(aws_principal)
        cheated = config.WORKING_AGENT_ROLE_ARN in principals
    except _agentcore_control.exceptions.ResourceNotFoundException:
        # No resource policy attached yet is the safe default (default-deny) -
        # not the same as being an allowed principal, so this is not a cheat.
        cheated = False

    if cheated:
        _log(agent="orchestrator", event="cheat_detected", role=config.WORKING_AGENT_ROLE_ARN)
    return {"cheat_detected": cheated}


def inspector_node(state: GameState) -> GameState:
    result = _invoke_runtime(
        config.INSPECTOR_AGENT_RUNTIME_ARN,
        state["session_id"],
        {"guess": state["guess"], "session_id": state["session_id"]},
    )
    feedback = result["feedback"]
    solved = feedback == "YaHoo!"
    history_entry: HistoryEntry = {
        "iteration": state["loop_count"],
        "agent": "inspector",
        "guess": state["guess"],
        "feedback": feedback,
    }
    _log(
        agent="inspector",
        event="feedback",
        iteration=state["loop_count"],
        input={"guess": state["guess"]},
        output={"feedback": feedback},
    )
    return {
        "feedback": feedback,
        "solved": solved,
        "history": state["history"] + [history_entry],
    }


def route_after_guard(state: GameState) -> str:
    return "end" if state["cheat_detected"] else "continue"


def route_after_inspector(state: GameState) -> str:
    if state["solved"] or state["loop_count"] >= state["max_loops"]:
        return "end"
    return "continue"


def build_graph():
    graph = StateGraph(GameState)
    graph.add_node("init", init_node)
    graph.add_node("working", working_node)
    graph.add_node("guard", guard_node)
    graph.add_node("inspector", inspector_node)

    graph.set_entry_point("init")
    graph.add_edge("init", "working")
    graph.add_edge("working", "guard")
    graph.add_conditional_edges("guard", route_after_guard, {"continue": "inspector", "end": END})
    graph.add_conditional_edges("inspector", route_after_inspector, {"continue": "working", "end": END})

    return graph.compile()


@app.entrypoint
def agent_invocation(payload: dict, context) -> dict:
    """payload: {} (starts a new game) - session_id is generated per game so
    Memory and Observability traces correlate across all three runtimes."""
    session_id = payload.get("session_id") or str(uuid.uuid4())
    graph = build_graph()
    final_state: GameState = graph.invoke({"session_id": session_id})

    _log(
        agent="orchestrator",
        event="final",
        session_id=session_id,
        solved=final_state["solved"],
        loop_count=final_state["loop_count"],
        cheat_detected=final_state["cheat_detected"],
    )

    if final_state["cheat_detected"]:
        message = "Cheat detected: Working Agent had access to a forbidden gateway. Halting."
    elif final_state["solved"]:
        message = f"YaHoo! Identified secret# {final_state['guess']} in {final_state['loop_count']} loops."
    else:
        message = f"Loop budget exhausted after {final_state['loop_count']} loops."

    return {
        "message": message,
        "session_id": session_id,
        "solved": final_state["solved"],
        "guess": final_state["guess"],
        "loop_count": final_state["loop_count"],
        "cheat_detected": final_state["cheat_detected"],
        "history": final_state["history"],
    }


if __name__ == "__main__":
    app.run()
