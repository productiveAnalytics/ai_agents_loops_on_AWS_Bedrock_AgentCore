"""Orchestrator Agent runtime config - read from env vars injected at deploy time."""

import os

AWS_REGION = os.environ["AWS_REGION"]

WORKING_AGENT_RUNTIME_ARN = os.environ["WORKING_AGENT_RUNTIME_ARN"]
INSPECTOR_AGENT_RUNTIME_ARN = os.environ["INSPECTOR_AGENT_RUNTIME_ARN"]

# For the cheat-boundary self-check: confirm, via a live GetResourcePolicy
# call, that the Working Agent's role is never an allowed principal on the
# Inspector's gateway.
WORKING_AGENT_ROLE_ARN = os.environ["WORKING_AGENT_ROLE_ARN"]
INSPECTOR_AGENT_GATEWAY_ARN = os.environ["INSPECTOR_AGENT_GATEWAY_ARN"]

MAX_LOOPS_PARAM_NAME = os.environ["MAX_LOOPS_PARAM_NAME"]

MIN_GUESS = int(os.environ.get("MIN_GUESS", "1"))
MAX_GUESS = int(os.environ.get("MAX_GUESS", "100"))
