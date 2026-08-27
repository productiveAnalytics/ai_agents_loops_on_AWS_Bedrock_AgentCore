# Number-Guessing Multi-Agent Loop on Amazon Bedrock AgentCore

## Context

The existing project at [`ai_agents_loops`](../ai_agents_loops) implements a guess/verify loop (Working Agent guesses via randomized search with no file access; Inspector Agent holds the secret and gives child-language hints; a LangGraph orchestrator wires them into a loop with a budget, a cheat guard, and JSONL logging) using LangChain + LangGraph, running as one local Python process. This project redeploys it as a **production-style, fully AWS-native multi-agent system on Amazon Bedrock AgentCore**, using AgentCore's **Runtime**, **Memory**, **Gateway** (tools), **Observability**, **Guardrails**, and **Evaluations** modules — not just relocating the code, but using each service the way it's meant to be used.

Key decisions made during design:
- **Bedrock-hosted Claude** (`us.anthropic.claude-haiku-4-5-20251001-v1:0`) instead of direct Anthropic API calls, via `langchain-aws`'s `init_chat_model(..., model_provider="bedrock_converse")`.
- **Full physical isolation**: Working Agent, Inspector Agent, and Orchestrator each deploy as their own AgentCore Runtime agent.
- **Two separate Gateways**, each with exactly one Lambda-backed tool, so the "Working Agent can't read the secret" guarantee is an AWS IAM boundary, not just app-level trust.
- **AgentCore CLI** for deployment (not CDK, at least initially) — the CLI's `agentcore deploy` provisions everything via a CDK app it generates internally.
- Every resource is tagged `Project=number_guessing_ai_agents` for tag-based discovery and cleanup (`scripts/teardown.py`).

The original `ai_agents_loops` project is untouched — this is an independent sibling.

## What happens to the existing LangChain/LangGraph code

**Nothing is discarded — AgentCore is additive infrastructure wrapped around the same agent logic.**

| Original file | What happens to it |
|---|---|
| `agents/orchestrator.py` (LangGraph `StateGraph`) | Ported into [`app/orchestrator_agent/main.py`](app/orchestrator_agent/main.py). Same graph topology (init→working→guard→inspector→loop/end), same state shape. The `working`/`inspector` node bodies now call `InvokeAgentRuntime` instead of local function calls, since those became separate deployables. `guard` became a real `GetResourcePolicy` check instead of a local tool-list assertion. |
| `agents/working_agent.py` | Ported into [`app/working_agent/main.py`](app/working_agent/main.py). Same narrowing algorithm and `HintInterpretation` structured-output call. `generate_guess` is now an MCP tool loaded from this agent's own Gateway via `langchain-mcp-adapters`, resolving to a Lambda instead of local Python. |
| `agents/inspector_agent.py` | Ported into [`app/inspector_agent/main.py`](app/inspector_agent/main.py). `check_guess`'s comparison logic is untouched. `read_secret` is an MCP tool from this agent's own Gateway. The hint-generation call gained a native Guardrail (`guardrail_config`). |
| `agents/tools.py` | Split: `generate_guess`/`read_secret` bodies became [`lambdas/generate_guess/handler.py`](lambdas/generate_guess/handler.py) / [`lambdas/read_secret/handler.py`](lambdas/read_secret/handler.py) (the latter now reads SSM instead of a local file); `check_guess` copied verbatim into the Inspector's `main.py`. |
| `agents/tracing.py` (LangFuse) | Dropped — superseded by AgentCore Observability (automatic OTEL instrumentation on every Runtime, zero code). |
| `config.py`, `agent.properties` | Model ids move to `BEDROCK_MODEL_ID` env vars; `secret_value` moves to SSM Parameter Store (read only by `read_secret`); `max_loops` stays a simple SSM-backed value the Orchestrator reads directly. |

LangChain remains the library each agent uses for its LLM/tool calls; LangGraph remains the library the Orchestrator uses to run the loop. What changed is *where* each piece runs and *how* they reach their tools/each other.

## Architecture

### Runtime topology — 3 isolated AgentCore Runtime agents

```
                         agentcore invoke
                                │
                                ▼
             ┌─────────────────────────────────┐
             │  number_guessing_orchestrator_agent │  ← entrypoint; runs the LangGraph loop
             └──────────────┬──────────────────┘
                InvokeAgentRuntime (same session_id, for trace correlation)
          ┌─────────────────┴─────────────────┐
          ▼                                     ▼
  ┌───────────────────────┐            ┌───────────────────────┐
  │ number_guessing_working_agent │      │ number_guessing_inspector_agent │
  └───────────┬───────────┘            └───────────┬───────────┘
   InvokeGateway (own gateway ONLY)       InvokeGateway (own gateway ONLY)
              ▼                                     ▼
  ┌───────────────────────┐            ┌───────────────────────┐
  │ num-guess-working-gw   │            │ num-guess-inspector-gw │
  │  target: generate_guess│            │  target: read_secret   │
  └───────────────────────┘            └───────────────────────┘
```

- The Orchestrator never touches either Gateway directly — it only has `InvokeAgentRuntime` on the other two runtimes.
- The Working Agent's Runtime role has no permission on `num-guess-inspector-gw` at all — that request is rejected before it reaches `read_secret`, regardless of what the Working Agent's LLM attempts.
- All three runtimes are invoked with the same `runtimeSessionId` (one per game) so Observability shows one correlated trace.

### How each AgentCore module is used

- **Runtime** — 3 BYO/Container agents, `AWS_IAM` (SigV4) inbound auth (the schema default).
- **Gateway** — 2 Gateways, each with exactly one Lambda target, IAM-authorized.
- **Memory** — one short-term `number_guessing_game_memory` resource; Working/Inspector each write their turn via `MemorySessionManager`.
- **Guardrails** — a Bedrock Guardrail blocking any digit character in the Inspector's model output (a fully static rule — hints never legitimately need digits), applied natively via `guardrail_config` on the Bedrock Converse call.
- **Observability** — automatic OTEL instrumentation on all three Runtimes; correlated via the shared session id.
- **Evaluations** — a code-based (Lambda) evaluator, `number_guessing_no_secret_leak_evaluator`, that deterministically scans trace spans for the literal secret digits.

## Project layout

```
ai_agents_loops_on_AWS_Bedrock_AgentCore/
├── app/{working_agent,inspector_agent,orchestrator_agent}/   # the 3 agents: main.py, Dockerfile, requirements.txt, config.py
├── lambdas/{generate_guess,read_secret,no_secret_leak_evaluator}/handler.py
├── shared/                     # single source of truth: resource_config.py, iam_boundary.py, guardrail_config.py
├── deploy/agentcore-cli/
│   ├── bootstrap_commands.sh   # every `agentcore` CLI command used to build agentcore.json, in order
│   ├── deploy_lambdas.py       # boto3: creates the 3 Lambdas (CLI's non-interactive mode needs pre-existing ARNs)
│   ├── tool_schemas/           # MCP tool schema JSON for the 2 Gateway-target Lambdas
│   ├── post_deploy/create_guardrail.py
│   └── agentcore/              # the CLI project itself (agentcore.json, aws-targets.json, generated CDK app)
├── scripts/{setup_secret.py,teardown.py}
└── tests/{test_iam_boundary.py,test_no_digit_leak_local.py}
```

## Verification

1. `uv run pytest` — local, no AWS needed; exercises `shared/` logic directly.
2. `deploy/agentcore-cli/deploy_lambdas.py` — creates the 3 Lambdas + execution role.
3. Wire the Gateway targets/evaluator to those Lambda ARNs and hand-author the `connections`/env vars in `agentcore.json` (see `bootstrap_commands.sh`), then `agentcore validate`.
4. `post_deploy/create_guardrail.py`, then set the printed Guardrail ID/version into the Inspector's envVars.
5. `agentcore deploy -y` — the first genuinely billable/irreversible step; provisions everything.
6. `scripts/setup_secret.py --secret-value 53 --max-loops 8` to seed SSM.
7. Invoke the orchestrator runtime — confirm a full game runs to `YaHoo!` or loop-budget-exhausted.
8. Confirm the Working Agent's role cannot invoke the Inspector's gateway (the actual IAM boundary).
9. `scripts/teardown.py --yes` to tear everything down and stop billing once confirmed working.
