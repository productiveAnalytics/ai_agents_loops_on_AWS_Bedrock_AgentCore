"""Full teardown: delete every AWS resource this project created, so nothing
keeps costing money after you're done experimenting.

Two categories of resource, handled differently:

1. Resources owned by the AgentCore CDK-generated CloudFormation stack
   (`AgentCore-agentcorecli-default`) - the 3 Runtimes, 2 Gateways+targets,
   Memory, Evaluator, 3 ECR repos, 10 IAM roles+policies, 3 KMS keys, 3
   internal Lambdas, and the CodeBuild project. Deleting the stack removes
   all of these in dependency order - confirmed via
   `list_stack_resources` against the live stack, not assumed. ECR repos
   are emptied first since CloudFormation can't delete a non-empty
   repository. The 3 KMS keys get an explicit post-delete check, since
   they - like IAM roles - never receive the `aws:cloudformation:*` tags
   CFN normally stamps on its resources, and their DeletionPolicy could in
   principle be Retain.

2. Resources this project's own scripts created directly via boto3, outside
   any CloudFormation stack: the Guardrail, the 2 SSM parameters, the 3
   "real" Lambdas (generate_guess/read_secret/no_secret_leak_evaluator) and
   their shared execution role. Tag-discovered via
   `shared.resource_config.PROJECT_TAG` and deleted individually.

CloudWatch log groups are neither: they're auto-created outside the
template (by Lambda/CodeBuild/the AgentCore Runtime service on first use),
so stack deletion won't touch most of them. Swept and deleted separately by
name pattern, regardless of stack ownership.

Defaults to --dry-run (lists what it found, deletes nothing).
Pass --yes to actually delete.

Usage:
    uv run python scripts/teardown.py            # dry run
    uv run python scripts/teardown.py --yes       # actually delete
"""

import argparse
import sys
import time
from pathlib import Path

import boto3

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared.resource_config import AWS_PROFILE, AWS_REGION, PROJECT_TAG  # noqa: E402

_SERVICE_KEY, _SERVICE_VALUE = next(iter(PROJECT_TAG.items()))

STACK_NAME = "AgentCore-agentcorecli-default"
LOG_GROUP_NAME_PATTERNS = ("agentcorecli", "number-guessing", "number_guessing")

# Resource types whose lifecycle is fully owned by STACK_NAME for this
# project (confirmed live via list_stack_resources) - excluded from the
# generic tag-based individual-delete path so we don't fight the stack.
_STACK_OWNED_SERVICES = {"ecr", "kms", "codebuild", "cloudformation", "bedrock-agentcore"}


def _find_tagged_resources(session) -> list[str]:
    client = session.client("resourcegroupstaggingapi")
    arns = []
    paginator = client.get_paginator("get_resources")
    for page in paginator.paginate(TagFilters=[{"Key": _SERVICE_KEY, "Values": [_SERVICE_VALUE]}]):
        arns.extend(m["ResourceARN"] for m in page["ResourceTagMappingList"])
    arns.extend(_find_tagged_iam_roles(session))
    return arns


def _find_tagged_iam_roles(session) -> list[str]:
    # IAM is not a searchable resource type in the Resource Groups Tagging
    # API (confirmed live: get_resources returns nothing for IAM even with
    # ResourceTypeFilters=["iam"], despite the roles genuinely carrying the
    # tag) - so IAM roles need their own direct discovery via list_role_tags.
    iam = session.client("iam")
    arns = []
    paginator = iam.get_paginator("list_roles")
    for page in paginator.paginate():
        for role in page["Roles"]:
            tags = iam.list_role_tags(RoleName=role["RoleName"]).get("Tags", [])
            if any(t["Key"] == _SERVICE_KEY and t["Value"] == _SERVICE_VALUE for t in tags):
                arns.append(role["Arn"])
    return arns


def _service_of(arn: str) -> str:
    # arn:aws:<service>:<region>:<account>:<resource>
    return arn.split(":")[2]


def _stack_owned_role_and_lambda_names(cfn) -> tuple[set[str], set[str]]:
    role_names, lambda_names = set(), set()
    try:
        paginator = cfn.get_paginator("list_stack_resources")
        for page in paginator.paginate(StackName=STACK_NAME):
            for r in page["StackResourceSummaries"]:
                pid = r.get("PhysicalResourceId")
                if not pid:
                    continue
                if r["ResourceType"] == "AWS::IAM::Role":
                    role_names.add(pid)
                elif r["ResourceType"] == "AWS::Lambda::Function":
                    lambda_names.add(pid)
    except cfn.exceptions.ClientError:
        pass  # stack doesn't exist (already torn down)
    return role_names, lambda_names


def _stack_kms_key_ids(cfn) -> list[str]:
    key_ids = []
    try:
        paginator = cfn.get_paginator("list_stack_resources")
        for page in paginator.paginate(StackName=STACK_NAME):
            for r in page["StackResourceSummaries"]:
                if r["ResourceType"] == "AWS::KMS::Key" and r.get("PhysicalResourceId"):
                    key_ids.append(r["PhysicalResourceId"])
    except cfn.exceptions.ClientError:
        pass
    return key_ids


def _delete_matching_log_groups(logs_client, dry_run: bool) -> None:
    paginator = logs_client.get_paginator("describe_log_groups")
    for page in paginator.paginate():
        for lg in page["logGroups"]:
            name = lg["logGroupName"]
            if not any(p in name for p in LOG_GROUP_NAME_PATTERNS):
                continue
            print(f"{'[dry-run] would delete log group' if dry_run else 'Deleting log group'}: {name}")
            if not dry_run:
                logs_client.delete_log_group(logGroupName=name)


def _empty_matching_ecr_repos(ecr_client, dry_run: bool) -> None:
    paginator = ecr_client.get_paginator("describe_repositories")
    for page in paginator.paginate():
        for repo in page["repositories"]:
            name = repo["repositoryName"]
            if not any(p in name for p in LOG_GROUP_NAME_PATTERNS):
                continue
            images = ecr_client.list_images(repositoryName=name)["imageIds"]
            if not images:
                continue
            print(f"{'[dry-run] would empty' if dry_run else 'Emptying'} ECR repo {name}: {len(images)} image(s)")
            if not dry_run:
                ecr_client.batch_delete_image(repositoryName=name, imageIds=images)


def _delete_stack(cfn, dry_run: bool) -> None:
    try:
        cfn.describe_stacks(StackName=STACK_NAME)
    except cfn.exceptions.ClientError:
        print(f"CloudFormation stack {STACK_NAME} not found (already deleted).")
        return

    print(f"{'[dry-run] would delete' if dry_run else 'Deleting'} CloudFormation stack: {STACK_NAME}")
    print("  (removes the 3 Runtimes, 2 Gateways+targets, Memory, Evaluator, 3 ECR repos,")
    print("   10 IAM roles+policies, 3 KMS keys, 3 internal Lambdas, and the CodeBuild project)")
    if dry_run:
        return

    cfn.delete_stack(StackName=STACK_NAME)
    print("  waiting for stack deletion to complete (this can take several minutes)...")
    try:
        cfn.get_waiter("stack_delete_complete").wait(
            StackName=STACK_NAME, WaiterConfig={"Delay": 15, "MaxAttempts": 80}
        )
        print("  stack deleted.")
    except Exception as exc:  # noqa: BLE001 - report and continue, don't abort the rest of teardown
        print(f"  WARNING: stack deletion did not complete cleanly: {exc}")
        print(f"  check the CloudFormation console/events for {STACK_NAME} for the failure reason.")


def _ensure_kms_keys_scheduled(kms_client, key_ids: list[str], dry_run: bool) -> None:
    for key_id in key_ids:
        try:
            state = kms_client.describe_key(KeyId=key_id)["KeyMetadata"]["KeyState"]
        except kms_client.exceptions.NotFoundException:
            continue
        if state == "PendingDeletion":
            print(f"KMS key {key_id}: already pending deletion.")
            continue
        print(
            f"{'[dry-run] would schedule deletion of' if dry_run else 'Scheduling deletion of'} "
            f"KMS key {key_id} (state={state}, 7-day minimum pending window - AWS still bills "
            "during that window regardless of this script)"
        )
        if not dry_run:
            kms_client.schedule_key_deletion(KeyId=key_id, PendingWindowInDays=7)


def _delete_standalone(session, arn: str, dry_run: bool) -> None:
    service = _service_of(arn)
    print(f"{'[dry-run] would delete' if dry_run else 'Deleting'}: {arn}")
    if dry_run:
        return

    if service == "lambda":
        name = arn.split(":")[-1]
        session.client("lambda").delete_function(FunctionName=name)
    elif service == "iam":
        name = arn.split("/")[-1]
        iam = session.client("iam")
        for policy in iam.list_role_policies(RoleName=name).get("PolicyNames", []):
            iam.delete_role_policy(RoleName=name, PolicyName=policy)
        for policy in iam.list_attached_role_policies(RoleName=name).get("AttachedPolicies", []):
            iam.detach_role_policy(RoleName=name, PolicyArn=policy["PolicyArn"])
        iam.delete_role(RoleName=name)
    elif service == "bedrock":
        guardrail_id = arn.split("/")[-1]
        session.client("bedrock").delete_guardrail(guardrailIdentifier=guardrail_id)
    elif service == "ssm":
        name = "/" + arn.split(":parameter/", 1)[-1]
        session.client("ssm").delete_parameter(Name=name)
    else:
        print(f"  (no delete handler for service '{service}' - remove manually)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yes", action="store_true", help="Actually delete (default is dry-run)")
    args = parser.parse_args()
    dry_run = not args.yes

    session = boto3.Session(profile_name=AWS_PROFILE, region_name=AWS_REGION)
    cfn = session.client("cloudformation")
    ecr = session.client("ecr")
    logs_client = session.client("logs")
    kms = session.client("kms")

    stack_role_names, stack_lambda_names = _stack_owned_role_and_lambda_names(cfn)
    stack_kms_key_ids = _stack_kms_key_ids(cfn)

    print("=== CloudWatch log groups (auto-created, not part of the CFN template) ===")
    _delete_matching_log_groups(logs_client, dry_run)

    print("\n=== ECR repositories (emptying images so stack deletion isn't blocked) ===")
    _empty_matching_ecr_repos(ecr, dry_run)

    print("\n=== CloudFormation stack ===")
    _delete_stack(cfn, dry_run)
    if not dry_run:
        time.sleep(5)  # let KMS/IAM eventual consistency settle before the safety-net check

    # Confirmed live: the 3 CDK container-image-builder custom-resource Lambdas get
    # invoked one more time as part of the stack's own DELETE cleanup, which
    # auto-recreates their /aws/lambda/<name> log group (Lambda does this on any
    # invoke if the log group is missing) - so the first sweep above can be
    # undone by the stack deletion itself. Sweep again now that it's done.
    print("\n=== CloudWatch log groups, second pass (stack deletion can recreate some) ===")
    _delete_matching_log_groups(logs_client, dry_run)

    print("\n=== KMS keys (safety net in case the stack's DeletionPolicy retained them) ===")
    _ensure_kms_keys_scheduled(kms, stack_kms_key_ids, dry_run)

    print(f"\n=== Standalone resources tagged {_SERVICE_KEY}={_SERVICE_VALUE} (not part of the stack) ===")
    arns = _find_tagged_resources(session)
    standalone = [
        arn
        for arn in arns
        if _service_of(arn) not in _STACK_OWNED_SERVICES and _service_of(arn) != "logs"
        and not (_service_of(arn) == "iam" and arn.split("/")[-1] in stack_role_names)
        and not (_service_of(arn) == "lambda" and arn.split(":")[-1] in stack_lambda_names)
    ]
    if not standalone:
        print("(none found)")
    for arn in standalone:
        _delete_standalone(session, arn, dry_run)

    if dry_run:
        print("\nDry run only - re-run with --yes to actually delete everything listed above.")


if __name__ == "__main__":
    main()
