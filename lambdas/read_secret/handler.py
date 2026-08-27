"""Gateway Lambda target for the `read_secret` tool.

Bound only to inspector-agent-gateway - the Working Agent's Runtime role has
no InvokeGateway permission on this gateway at all, so this is the only path
to the secret value in the whole system.

The SSM parameter name comes from an env var (set at deploy time from
shared/resource_config.py's SECRET_VALUE_PARAM) rather than being imported
directly, so this Lambda ships as a standalone zip with no project-local
dependencies.
"""

import os

import boto3


def handler(event: dict, context) -> dict:
    # Lazy client construction keeps this module import-safe for local unit
    # tests (no AWS region/credentials needed just to import the file).
    ssm = boto3.client("ssm")
    param_name = os.environ["SECRET_VALUE_PARAM_NAME"]
    response = ssm.get_parameter(Name=param_name, WithDecryption=True)
    return {"secret_value": int(response["Parameter"]["Value"])}
