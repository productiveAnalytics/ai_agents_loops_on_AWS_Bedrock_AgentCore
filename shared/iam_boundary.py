"""Builds the exact IAM resource-based policy documents that enforce the
cheat boundary: each Gateway's resource policy names exactly one principal
(its own Runtime agent's execution role), never the other agent's role.

Plain Python, no AWS SDK imports - both the AgentCore CLI deploy path's
post_deploy/attach_resource_policies.py and (later) a CDK custom resource
call these functions directly, so the policy logic is defined once.
"""

from __future__ import annotations


def build_gateway_resource_policy(gateway_arn: str, allowed_principal_role_arn: str) -> dict:
    """Return an IAM resource-based policy document granting InvokeGateway on
    `gateway_arn` to exactly one principal, `allowed_principal_role_arn`.

    Per AWS's resource-based-policy contract for AgentCore: SigV4-authenticated
    resources use a specific IAM principal ARN (not a wildcard), and the
    Resource field must be the exact ARN of the resource the policy is
    attached to.
    """
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AllowOwnRuntimeOnly",
                "Effect": "Allow",
                "Principal": {"AWS": allowed_principal_role_arn},
                "Action": "bedrock-agentcore:InvokeGateway",
                "Resource": gateway_arn,
            }
        ],
    }


def extract_principal_arns(policy: dict) -> set[str]:
    """Collect every principal ARN named anywhere in a policy document's
    Allow statements, regardless of whether Principal.AWS is a string or a
    list."""
    principals: set[str] = set()
    for statement in policy.get("Statement", []):
        if statement.get("Effect") != "Allow":
            continue
        aws_principal = statement.get("Principal", {})
        if isinstance(aws_principal, dict):
            aws_value = aws_principal.get("AWS")
            if isinstance(aws_value, str):
                principals.add(aws_value)
            elif isinstance(aws_value, list):
                principals.update(aws_value)
    return principals


def assert_single_principal(policy: dict, expected_principal_role_arn: str) -> None:
    """Raise AssertionError unless `policy` allows exactly one principal, and
    it is `expected_principal_role_arn`. This is the pre-deploy, no-AWS-needed
    version of the Orchestrator's own runtime GetResourcePolicy check."""
    principals = extract_principal_arns(policy)
    if principals != {expected_principal_role_arn}:
        raise AssertionError(
            f"Expected exactly one allowed principal ({expected_principal_role_arn}), "
            f"found {principals or 'none'}"
        )
