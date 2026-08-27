"""Tag-based cleanup: find every resource tagged Project=number_guessing_ai_agents
via the Resource Groups Tagging API and delete it. Independent of
agentcore.json's local state, so it still works even if that file is stale.

Also deletes the two SSM parameters (tagged the same way) and the Guardrail
(a plain Bedrock resource, also tag-discoverable).

Defaults to --dry-run (lists what it found, deletes nothing).
Pass --yes to actually delete.

Usage:
    uv run python scripts/teardown.py            # dry run
    uv run python scripts/teardown.py --yes       # actually delete
"""

import argparse
import sys
from pathlib import Path

import boto3

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared.resource_config import AWS_PROFILE, AWS_REGION, PROJECT_TAG  # noqa: E402

_SERVICE_KEY, _SERVICE_VALUE = next(iter(PROJECT_TAG.items()))


def _find_tagged_resources(session) -> list[str]:
    client = session.client("resourcegroupstaggingapi")
    arns = []
    paginator = client.get_paginator("get_resources")
    for page in paginator.paginate(TagFilters=[{"Key": _SERVICE_KEY, "Values": [_SERVICE_VALUE]}]):
        arns.extend(m["ResourceARN"] for m in page["ResourceTagMappingList"])
    return arns


def _service_of(arn: str) -> str:
    # arn:aws:<service>:<region>:<account>:<resource>
    return arn.split(":")[2]


def _delete(session, arn: str, dry_run: bool) -> None:
    service = _service_of(arn)
    print(f"{'[dry-run] would delete' if dry_run else 'Deleting'}: {arn}")
    if dry_run:
        return

    if service == "lambda":
        name = arn.split(":")[-1]
        session.client("lambda").delete_function(FunctionName=name)
    elif service == "iam":
        # role ARNs: arn:aws:iam::<account>:role/<name>
        name = arn.split("/")[-1]
        iam = session.client("iam")
        for policy in iam.list_role_policies(RoleName=name).get("PolicyNames", []):
            iam.delete_role_policy(RoleName=name, PolicyName=policy)
        for policy in iam.list_attached_role_policies(RoleName=name).get("AttachedPolicies", []):
            iam.detach_role_policy(RoleName=name, PolicyArn=policy["PolicyArn"])
        iam.delete_role(RoleName=name)
    elif service == "bedrock-agentcore":
        _delete_agentcore_resource(session, arn)
    elif service == "bedrock":
        # guardrail ARN: arn:aws:bedrock:<region>:<account>:guardrail/<id>
        guardrail_id = arn.split("/")[-1]
        session.client("bedrock").delete_guardrail(guardrailIdentifier=guardrail_id)
    elif service == "ssm":
        name = "/" + arn.split(":parameter/", 1)[-1]
        session.client("ssm").delete_parameter(Name=name)
    else:
        print(f"  (no delete handler for service '{service}' - remove manually)")


def _delete_agentcore_resource(session, arn: str) -> None:
    control = session.client("bedrock-agentcore-control")
    resource_type = arn.split(":")[5].split("/")[0]
    resource_id = arn.split("/")[-1]
    if resource_type == "runtime":
        control.delete_agent_runtime(agentRuntimeId=resource_id)
    elif resource_type == "gateway":
        control.delete_gateway(gatewayIdentifier=resource_id)
    elif resource_type == "memory":
        control.delete_memory(memoryId=resource_id)
    elif resource_type == "evaluator":
        control.delete_evaluator(evaluatorId=resource_id)
    else:
        print(f"  (no delete handler for bedrock-agentcore resource type '{resource_type}')")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yes", action="store_true", help="Actually delete (default is dry-run)")
    args = parser.parse_args()

    session = boto3.Session(profile_name=AWS_PROFILE, region_name=AWS_REGION)
    arns = _find_tagged_resources(session)

    if not arns:
        print(f"No resources found tagged {_SERVICE_KEY}={_SERVICE_VALUE}.")
        return

    print(f"Found {len(arns)} resource(s) tagged {_SERVICE_KEY}={_SERVICE_VALUE}:\n")
    for arn in arns:
        _delete(session, arn, dry_run=not args.yes)

    if not args.yes:
        print("\nDry run only - re-run with --yes to actually delete.")
        print("Note: SSM parameters and any un-tag-discoverable resources (e.g. gateway targets)")
        print("may need manual cleanup - check the AWS console/CLI to confirm nothing is left running.")


if __name__ == "__main__":
    main()
