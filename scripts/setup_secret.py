"""One-time setup: write secret_value (SecureString) and max_loops (String)
into SSM Parameter Store. Deliberately kept out of agentcore.json/CDK so the
secret's actual value never appears in a checked-in config file or
CloudFormation template.

Usage:
    uv run python scripts/setup_secret.py --secret-value 53 --max-loops 8
"""

import argparse
import sys
from pathlib import Path

import boto3

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared.resource_config import (  # noqa: E402
    AWS_PROFILE,
    AWS_REGION,
    MAX_LOOPS_PARAM,
    PROJECT_TAG,
    SECRET_VALUE_PARAM,
)

_TAGS = [{"Key": k, "Value": v} for k, v in PROJECT_TAG.items()]


def _put_param(ssm, name: str, value: str, param_type: str) -> None:
    is_new = not _param_exists(ssm, name)
    ssm.put_parameter(Name=name, Value=value, Type=param_type, Overwrite=True)
    if is_new:
        ssm.add_tags_to_resource(ResourceType="Parameter", ResourceId=name, Tags=_TAGS)


def _param_exists(ssm, name: str) -> bool:
    try:
        ssm.get_parameter(Name=name)
        return True
    except ssm.exceptions.ParameterNotFound:
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--secret-value", type=int, required=True, help="The number to guess")
    parser.add_argument("--max-loops", type=int, required=True, help="Loop budget for the Orchestrator")
    args = parser.parse_args()

    ssm = boto3.Session(profile_name=AWS_PROFILE, region_name=AWS_REGION).client("ssm")
    _put_param(ssm, SECRET_VALUE_PARAM, str(args.secret_value), "SecureString")
    _put_param(ssm, MAX_LOOPS_PARAM, str(args.max_loops), "String")

    print(f"Set {SECRET_VALUE_PARAM} (SecureString) and {MAX_LOOPS_PARAM}={args.max_loops}.")


if __name__ == "__main__":
    main()
