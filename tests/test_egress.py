"""The egress boundary.

Three claims are asserted here, because all three are security claims:
  1. Nothing reaches the network unless egress is explicitly enabled.
  2. Snippets are redacted before they are put in a payload.
  3. Token and spend ceilings are enforced in Python.
"""

import socket

import pytest

from orgono.app.core.config import Config, EgressPolicy
from orgono.app.core.egress import (
    EgressRefused,
    build_plan,
    execute,
    redacted_plan_for_display,
)
from orgono.app.core.query import QueryResult


@pytest.fixture
def no_network(monkeypatch):
    """Make any real socket connection an immediate, loud failure."""
    def boom(*a, **k):
        raise AssertionError("a network connection was attempted")
    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    return True


def _result_with_secret():
    r = QueryResult(operation="impact")
    r.nodes = [{"id": "a", "kind": "function", "name": "load", "path": "a.py",
                "start_line": 1, "end_line": 2, "language": "python"}]
    r.snippets = {"a": ["API_KEY = 'sk-proj-abcdefghijklmnop1234567890'",
                        "db = 'postgres://u:supersecretpw@h/db'"]}
    return r


def test_building_a_graph_needs_no_network(no_network, golden_dir, silent_log):
    from orgono.app.core.extract import extract_repo
    graph = extract_repo(golden_dir, Config(), silent_log)
    assert len(graph.nodes) > 0


def test_egress_disabled_by_default():
    assert EgressPolicy().enabled is False
    assert EgressPolicy().dry_run is True
    assert Config().egress.enabled is False


def test_execute_refuses_when_disabled(no_network):
    policy = EgressPolicy(enabled=False)
    plan = build_plan("q", _result_with_secret(), None, policy, api_key="k")
    with pytest.raises(EgressRefused, match="egress is disabled"):
        execute(plan, policy, "k")


def test_execute_refuses_while_dry_run(no_network):
    policy = EgressPolicy(enabled=True, dry_run=True)
    plan = build_plan("q", _result_with_secret(), None, policy, api_key="k")
    with pytest.raises(EgressRefused, match="dry-run"):
        execute(plan, policy, "k")


def test_execute_refuses_without_a_key(no_network):
    policy = EgressPolicy(enabled=True, dry_run=False)
    plan = build_plan("q", _result_with_secret(), None, policy, api_key=None)
    with pytest.raises(EgressRefused, match="no API key"):
        execute(plan, policy, None)


def test_planted_secrets_are_redacted_before_egress(no_network):
    policy = EgressPolicy(enabled=True, redact=True)
    plan = build_plan("what loads config?", _result_with_secret(), None, policy, api_key="k")
    blob = str(plan.body)
    assert "sk-proj-abcdefghijklmnop1234567890" not in blob
    assert "supersecretpw" not in blob
    assert plan.redactions >= 2


def test_dry_run_payload_is_inspectable_and_masks_the_key(no_network):
    policy = EgressPolicy(enabled=True)
    plan = build_plan("q", _result_with_secret(), None, policy, api_key="sk-or-v1-realkey123456")
    shown = redacted_plan_for_display(plan)
    # redact_obj replaces the whole value because the *key* is secret-shaped,
    # which is stricter than masking just the token. Assert the property that
    # matters -- the real key is not present -- not the exact formatting.
    assert "[REDACTED]" in shown["headers"]["Authorization"]
    assert "realkey" not in shown["headers"]["Authorization"]
    assert "sk-or-v1-realkey123456" not in str(shown)
    assert shown["body"]["messages"][0]["role"] == "system"
    assert shown["format"] == "orgono-openrouter-chat/1"


def test_prompt_token_ceiling_is_enforced(no_network):
    policy = EgressPolicy(enabled=True, max_prompt_tokens=200)
    big = QueryResult(operation="impact")
    big.snippets = {f"n{i}": ["x" * 200] for i in range(200)}
    plan = build_plan("q", big, None, policy, api_key="k")
    assert plan.estimated_prompt_tokens <= policy.max_prompt_tokens
    assert any("trimmed" in n for n in plan.notes)


def test_spend_ceiling_refuses(no_network):
    policy = EgressPolicy(enabled=True, max_prompt_tokens=10_000_000,
                          max_completion_tokens=1_000_000, max_spend_usd=0.000001)
    with pytest.raises(EgressRefused, match="spend ceiling"):
        build_plan("q", _result_with_secret(), None, policy, api_key="k")


def test_payload_format_is_documented_and_stable(no_network):
    plan = build_plan("q", _result_with_secret(), None, EgressPolicy(enabled=True), api_key="k")
    assert plan.format == "orgono-openrouter-chat/1"
    assert plan.url.endswith("/chat/completions")
    assert set(plan.body) >= {"model", "messages", "max_tokens"}
