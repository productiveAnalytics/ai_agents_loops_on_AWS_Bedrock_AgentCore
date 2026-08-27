"""Single source of truth for every resource name/id this project creates.

Both the AgentCore CLI deploy path (deploy/agentcore-cli/) and, later, a CDK
path import from here so names never drift between provisioning mechanisms.
"""

import os

# --- Tagging (used for tag-based discovery/cleanup by scripts/teardown.py) ---
PROJECT_TAG_KEY = "Project"
PROJECT_TAG_VALUE = "number_guessing_ai_agents"
PROJECT_TAG = {PROJECT_TAG_KEY: PROJECT_TAG_VALUE}

# --- AWS account/region ---
# prod8ctive's configured default region - keep everything in this region so
# Gateways/Runtimes/Lambdas/Memory/Guardrail are all co-located.
AWS_REGION = os.getenv("AWS_REGION", "us-west-2")
AWS_PROFILE = os.getenv("AWS_PROFILE", "prod8ctive")

# --- Model hosting ---
# Confirmed available in the target account/region (aws bedrock list-inference-profiles).
BEDROCK_MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0")

# --- Game parameters (mirrors the original project's config.py) ---
MIN_GUESS = 1
MAX_GUESS = 100

# --- Runtime agent names ---
# AgentCore runtime/memory/evaluator names must match ^[a-zA-Z][a-zA-Z0-9_]{0,47}$
# (letters/digits/underscore only, no hyphens) - confirmed against the
# agentcore CLI's agentcore.json schema.
WORKING_AGENT_RUNTIME_NAME = "number_guessing_working_agent"
INSPECTOR_AGENT_RUNTIME_NAME = "number_guessing_inspector_agent"
ORCHESTRATOR_AGENT_RUNTIME_NAME = "number_guessing_orchestrator_agent"

# --- Gateway names (one per tool, for IAM-enforced isolation) ---
# Gateway names allow hyphens (^[0-9a-zA-Z](?:[0-9a-zA-Z-]*[0-9a-zA-Z])?$), but
# AWS caps the *effective* gateway name (agentcore CLI project-name prefix +
# this name) at 48 chars - confirmed live against the "agentcorecli" project
# prefix, so kept short.
WORKING_AGENT_GATEWAY_NAME = "num-guess-working-gw"
INSPECTOR_AGENT_GATEWAY_NAME = "num-guess-inspector-gw"

# --- Gateway target / tool names ---
GENERATE_GUESS_TOOL_NAME = "generate_guess"
READ_SECRET_TOOL_NAME = "read_secret"

# --- Memory ---
MEMORY_NAME = "number_guessing_game_memory"
WORKING_AGENT_ACTOR_ID = "working-agent"
INSPECTOR_AGENT_ACTOR_ID = "inspector-agent"

# --- Guardrail (plain Bedrock resource, not agentcore.json-managed - name
# constraints are looser, but keep the underscore convention for consistency) ---
GUARDRAIL_NAME = "number-guessing-no-secret-leak-guardrail"

# --- Evaluator ---
EVALUATOR_NAME = "number_guessing_no_secret_leak_evaluator"

# --- SSM Parameter Store (secret_value is SecureString; never committed to the repo) ---
SSM_PARAM_PREFIX = "/number-guessing-ai-agents"
SECRET_VALUE_PARAM = f"{SSM_PARAM_PREFIX}/secret_value"
MAX_LOOPS_PARAM = f"{SSM_PARAM_PREFIX}/max_loops"
