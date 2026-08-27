"""Deploy the 3 Lambda functions (2 Gateway targets + 1 evaluator) via boto3.

The agentcore CLI's non-interactive `add gateway-target`/`add evaluator`
commands only accept a pre-existing Lambda ARN (--lambda-arn), not a
deploy-from-source path - so these 3 Lambdas are created here first, and
their ARNs are written to .deployed_lambdas.json for bootstrap_commands.sh's
later `agentcore add gateway-target`/`add evaluator` calls to consume.

Run with: uv run python deploy/agentcore-cli/deploy_lambdas.py
Idempotent: safe to re-run (updates function code/config if it already exists).
"""

import io
import json
import sys
import time
import zipfile
from pathlib import Path

import boto3

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from shared.resource_config import (  # noqa: E402
    AWS_PROFILE,
    AWS_REGION,
    MAX_LOOPS_PARAM,
    PROJECT_TAG,
    SECRET_VALUE_PARAM,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
LAMBDAS_DIR = REPO_ROOT / "lambdas"
MANIFEST_PATH = Path(__file__).resolve().parent / ".deployed_lambdas.json"

ROLE_NAME = "number-guessing-lambda-execution-role"

FUNCTIONS = {
    "generate_guess": "number-guessing-generate-guess",
    "read_secret": "number-guessing-read-secret",
    "no_secret_leak_evaluator": "number-guessing-no-secret-leak-evaluator",
}

# Env vars each Lambda needs at runtime (both read_secret and the evaluator
# read the secret from the same SSM parameter).
ENV_VARS_BY_FUNCTION = {
    "generate_guess": {},
    "read_secret": {"SECRET_VALUE_PARAM_NAME": SECRET_VALUE_PARAM},
    "no_secret_leak_evaluator": {"SECRET_VALUE_PARAM_NAME": SECRET_VALUE_PARAM},
}

TRUST_POLICY = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {"Service": "lambda.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }
    ],
}


def _ssm_read_policy(account_id: str) -> dict:
    param_arns = [
        f"arn:aws:ssm:{AWS_REGION}:{account_id}:parameter{SECRET_VALUE_PARAM}",
        f"arn:aws:ssm:{AWS_REGION}:{account_id}:parameter{MAX_LOOPS_PARAM}",
    ]
    return {
        "Version": "2012-10-17",
        "Statement": [
            {"Effect": "Allow", "Action": "ssm:GetParameter", "Resource": param_arns}
        ],
    }


def _zip_handler(handler_dir: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(handler_dir / "handler.py", arcname="handler.py")
    return buf.getvalue()


def _ensure_role(session, iam) -> str:
    try:
        role = iam.get_role(RoleName=ROLE_NAME)["Role"]
    except iam.exceptions.NoSuchEntityException:
        role = iam.create_role(
            RoleName=ROLE_NAME,
            AssumeRolePolicyDocument=json.dumps(TRUST_POLICY),
            Tags=[{"Key": k, "Value": v} for k, v in PROJECT_TAG.items()],
        )["Role"]
        iam.attach_role_policy(
            RoleName=ROLE_NAME,
            PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
        )
        # IAM role propagation delay before it's usable by Lambda.
        time.sleep(10)

    account_id = session.client("sts").get_caller_identity()["Account"]
    iam.put_role_policy(
        RoleName=ROLE_NAME,
        PolicyName="ReadNumberGuessingSsmParams",
        PolicyDocument=json.dumps(_ssm_read_policy(account_id)),
    )
    return role["Arn"]


def _tag_log_group(logs_client, function_name: str) -> None:
    # The /aws/lambda/<name> log group is auto-created by Lambda on first
    # invoke/deploy, not a CFN-declared resource, so it never picks up
    # project tags any other way. Safe to call before the log group exists
    # (Lambda creates it on deploy) - tag_log_group then just no-ops via the
    # ResourceNotFoundException below.
    try:
        logs_client.tag_log_group(logGroupName=f"/aws/lambda/{function_name}", tags=PROJECT_TAG)
    except logs_client.exceptions.ResourceNotFoundException:
        pass


def _ensure_function(lambda_client, name: str, zip_bytes: bytes, role_arn: str, env_vars: dict) -> str:
    try:
        lambda_client.get_function(FunctionName=name)
        lambda_client.update_function_code(FunctionName=name, ZipFile=zip_bytes)
        lambda_client.get_waiter("function_updated").wait(FunctionName=name)
        response = lambda_client.update_function_configuration(
            FunctionName=name, Role=role_arn, Timeout=30, Environment={"Variables": env_vars}
        )
        lambda_client.get_waiter("function_updated").wait(FunctionName=name)
    except lambda_client.exceptions.ResourceNotFoundException:
        response = lambda_client.create_function(
            FunctionName=name,
            Runtime="python3.12",
            Role=role_arn,
            Handler="handler.handler",
            Code={"ZipFile": zip_bytes},
            Timeout=30,
            Tags=PROJECT_TAG,
            Environment={"Variables": env_vars},
        )
        lambda_client.get_waiter("function_active_v2").wait(FunctionName=name)

    return response["FunctionArn"] if "FunctionArn" in response else lambda_client.get_function(
        FunctionName=name
    )["Configuration"]["FunctionArn"]


def main() -> None:
    session = boto3.Session(profile_name=AWS_PROFILE, region_name=AWS_REGION)
    iam = session.client("iam")
    lambda_client = session.client("lambda")
    logs_client = session.client("logs")

    role_arn = _ensure_role(session, iam)
    print(f"Execution role ready: {role_arn}")

    arns = {}
    for dir_name, function_name in FUNCTIONS.items():
        zip_bytes = _zip_handler(LAMBDAS_DIR / dir_name)
        env_vars = ENV_VARS_BY_FUNCTION[dir_name]
        arn = _ensure_function(lambda_client, function_name, zip_bytes, role_arn, env_vars)
        _tag_log_group(logs_client, function_name)
        arns[dir_name] = arn
        print(f"{function_name}: {arn}")

    MANIFEST_PATH.write_text(json.dumps(arns, indent=2))
    print(f"\nWrote {MANIFEST_PATH}")


if __name__ == "__main__":
    main()
