import re

from lambdas.no_secret_leak_evaluator.handler import _contains_secret
from shared.guardrail_config import build_guardrail_config

_DIGIT_PATTERN = re.compile(
    build_guardrail_config()["sensitiveInformationPolicyConfig"]["regexesConfig"][0]["pattern"]
)


def test_guardrail_pattern_matches_any_digit():
    assert _DIGIT_PATTERN.search("The secret is 42")
    assert _DIGIT_PATTERN.search("almost there, just 1 more step")


def test_guardrail_pattern_does_not_match_digit_free_hints():
    assert not _DIGIT_PATTERN.search("Choo choo train going very fast!")
    assert not _DIGIT_PATTERN.search("getting warm, tiny bit more!")


def test_guardrail_config_blocks_output_not_input():
    regex_config = build_guardrail_config()["sensitiveInformationPolicyConfig"]["regexesConfig"][0]
    assert regex_config["outputAction"] == "BLOCK"
    assert regex_config["outputEnabled"] is True
    assert regex_config["inputEnabled"] is False


def test_evaluator_detects_secret_in_nested_spans():
    secret = "42"
    spans = [{"attributes": {"gen_ai.completion": "The answer is 42, congrats!"}}]
    assert _contains_secret(spans, secret) is True


def test_evaluator_passes_when_secret_absent():
    secret = "42"
    spans = [{"attributes": {"gen_ai.completion": "Choo choo train going very fast!"}}]
    assert _contains_secret(spans, secret) is False


def test_evaluator_handles_empty_spans():
    assert _contains_secret([], "42") is False
