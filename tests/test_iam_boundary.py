import pytest

from shared.iam_boundary import (
    assert_single_principal,
    build_gateway_resource_policy,
    extract_principal_arns,
)

WORKING_ROLE = "arn:aws:iam::778858743114:role/number-guessing-working-agent-role"
INSPECTOR_ROLE = "arn:aws:iam::778858743114:role/number-guessing-inspector-agent-role"
INSPECTOR_GATEWAY_ARN = "arn:aws:bedrock-agentcore:us-west-2:778858743114:gateway/inspector-agent-gateway"


def test_build_gateway_resource_policy_names_exactly_one_principal():
    policy = build_gateway_resource_policy(INSPECTOR_GATEWAY_ARN, INSPECTOR_ROLE)
    assert extract_principal_arns(policy) == {INSPECTOR_ROLE}


def test_build_gateway_resource_policy_never_names_the_other_agents_role():
    policy = build_gateway_resource_policy(INSPECTOR_GATEWAY_ARN, INSPECTOR_ROLE)
    assert WORKING_ROLE not in extract_principal_arns(policy)


def test_build_gateway_resource_policy_uses_correct_action_and_resource():
    policy = build_gateway_resource_policy(INSPECTOR_GATEWAY_ARN, INSPECTOR_ROLE)
    statement = policy["Statement"][0]
    assert statement["Effect"] == "Allow"
    assert statement["Action"] == "bedrock-agentcore:InvokeGateway"
    assert statement["Resource"] == INSPECTOR_GATEWAY_ARN


def test_assert_single_principal_passes_for_correct_principal():
    policy = build_gateway_resource_policy(INSPECTOR_GATEWAY_ARN, INSPECTOR_ROLE)
    assert_single_principal(policy, INSPECTOR_ROLE)  # should not raise


def test_assert_single_principal_raises_for_wrong_principal():
    policy = build_gateway_resource_policy(INSPECTOR_GATEWAY_ARN, INSPECTOR_ROLE)
    with pytest.raises(AssertionError):
        assert_single_principal(policy, WORKING_ROLE)


def test_assert_single_principal_raises_if_cross_granted():
    """Regression guard: if a future change accidentally lists both agents'
    roles on one gateway's policy, this must fail loudly."""
    policy = build_gateway_resource_policy(INSPECTOR_GATEWAY_ARN, INSPECTOR_ROLE)
    policy["Statement"][0]["Principal"]["AWS"] = [INSPECTOR_ROLE, WORKING_ROLE]
    with pytest.raises(AssertionError):
        assert_single_principal(policy, INSPECTOR_ROLE)
