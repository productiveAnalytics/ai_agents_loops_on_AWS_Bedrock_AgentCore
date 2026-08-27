"""Tags every CloudWatch log group this project touches with PROJECT_TAG.

Log groups are auto-created (by Lambda on first invoke, by CodeBuild on
first build, by the AgentCore Runtime service on first invoke) rather than
declared as `AWS::Logs::LogGroup` resources in the CDK-synthesized template.
That means the project-level `tags` field in agentcore.json (applied via
CDK's `Tags.of(stack).add(...)`, which only stamps tags onto resources that
actually exist in the template) never reaches them - confirmed live: after
tagging the CloudFormation stack itself, the ECR repos/IAM roles/KMS keys/
CodeBuild project all picked up Project via that cascade, but every log
group under this stack still had zero tags. This script closes that gap
directly via `logs:TagLogGroup`, and is safe to re-run after every deploy
(idempotent - tagging just overwrites the same key/value).

Run with: uv run python deploy/agentcore-cli/post_deploy/tag_log_groups.py
"""

import sys
from pathlib import Path

import boto3

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from shared.resource_config import AWS_PROFILE, AWS_REGION, PROJECT_TAG  # noqa: E402

STACK_NAME = "AgentCore-agentcorecli-default"
TAGGABLE_RESOURCE_TYPES = {"AWS::Lambda::Function", "AWS::CodeBuild::Project"}


def _log_group_names_from_stack(cfn) -> list[str]:
    names = []
    paginator = cfn.get_paginator("list_stack_resources")
    for page in paginator.paginate(StackName=STACK_NAME):
        for resource in page["StackResourceSummaries"]:
            if resource["ResourceType"] not in TAGGABLE_RESOURCE_TYPES:
                continue
            physical_id = resource.get("PhysicalResourceId")
            if not physical_id:
                continue
            if resource["ResourceType"] == "AWS::Lambda::Function":
                names.append(f"/aws/lambda/{physical_id}")
            elif resource["ResourceType"] == "AWS::CodeBuild::Project":
                names.append(f"/aws/codebuild/{physical_id}")
    return names


def _log_group_names_from_runtimes(control) -> list[str]:
    names = []
    paginator = control.get_paginator("list_agent_runtimes")
    for page in paginator.paginate():
        for runtime in page["agentRuntimes"]:
            runtime_id = runtime["agentRuntimeId"]
            names.append(f"/aws/bedrock-agentcore/runtimes/{runtime_id}-DEFAULT")
    return names


def main() -> None:
    session = boto3.Session(profile_name=AWS_PROFILE, region_name=AWS_REGION)
    cfn = session.client("cloudformation")
    control = session.client("bedrock-agentcore-control")
    logs = session.client("logs")

    log_group_names = set(_log_group_names_from_stack(cfn)) | set(_log_group_names_from_runtimes(control))
    existing = {lg["logGroupName"] for lg in logs.describe_log_groups()["logGroups"]}

    for name in sorted(log_group_names):
        if name not in existing:
            print(f"skip (not created yet): {name}")
            continue
        logs.tag_log_group(logGroupName=name, tags=PROJECT_TAG)
        print(f"tagged: {name}")


if __name__ == "__main__":
    main()
