"""The query surface treats every caller as untrusted."""

import pytest

from orgono.app.core.config import QueryCaps
from orgono.app.core.query import QueryEngine, QueryRefused


@pytest.fixture
def engine(golden_graph, golden_dir, silent_log):
    return QueryEngine(golden_graph, caps=QueryCaps(), log=silent_log, root=golden_dir)


def test_find_symbol_without_a_selector_is_refused(engine):
    with pytest.raises(QueryRefused, match="no selector"):
        engine.find_symbol(caller="attacker")


def test_whole_graph_sweep_is_refused(golden_graph, golden_dir, silent_log):
    # 7 of 27 nodes is ~26%, so the threshold is set below that to exercise the
    # refusal rather than the happy path.
    caps = QueryCaps(bulk_refusal_min_nodes=1, bulk_refusal_ratio=0.05)
    eng = QueryEngine(golden_graph, caps=caps, log=silent_log, root=golden_dir)
    with pytest.raises(QueryRefused, match="bulk-export threshold"):
        eng.find_symbol(name="", kind="function", caller="attacker")


def test_node_cap_is_enforced_in_code(golden_graph, golden_dir, silent_log):
    caps = QueryCaps(max_nodes=3, bulk_refusal_ratio=1.0)
    eng = QueryEngine(golden_graph, caps=caps, log=silent_log, root=golden_dir)
    res = eng.find_symbol(kind="function", caller="t")
    assert len(res.nodes) <= 3
    assert res.truncated


def test_snippet_line_cap_is_enforced(golden_graph, golden_dir, silent_log):
    caps = QueryCaps(max_snippet_lines=2, bulk_refusal_ratio=1.0)
    eng = QueryEngine(golden_graph, caps=caps, log=silent_log, root=golden_dir)
    res = eng.find_symbol(name="query_users", include_snippets=True, caller="t")
    for lines in res.snippets.values():
        assert len(lines) <= 2


def test_depth_is_clamped_not_honoured(engine):
    res = engine.impact("query_users", direction="callers", depth=99, caller="t")
    assert any("clamped" in n for n in res.notes)


def test_impact_finds_callers(engine):
    res = engine.impact("query_users", direction="callers", depth=2, caller="t")
    names = {n["name"] for n in res.nodes}
    assert "find_by_email" in names


def test_impact_finds_callees(engine):
    res = engine.impact("query_users", direction="callees", depth=1, caller="t")
    names = {n["name"] for n in res.nodes}
    assert "connect" in names


def test_path_between_reports_a_real_chain(engine):
    res = engine.path_between("get_user_route", "query_users", max_depth=4, caller="t")
    assert res.edges, "expected a connecting path"
    for e in res.edges:
        assert e["path"] and e["line"] >= 1


def test_path_between_requires_both_ends(engine):
    with pytest.raises(QueryRefused):
        engine.path_between("", "x", caller="t")


def test_unknown_kind_is_a_value_error(engine):
    with pytest.raises(ValueError, match="invalid kind"):
        engine.find_symbol(name="x", kind="not_a_kind", caller="t")


def test_snippets_cannot_escape_the_repository_root(golden_graph, golden_dir, silent_log):
    from orgono.app.core.graph import Node
    engine = QueryEngine(golden_graph, log=silent_log, root=golden_dir)
    escaped = Node(id="x", kind="function", name="evil", path="../../../../etc/passwd",
                   start_line=1, end_line=3)
    assert engine.snippet_for(escaped) == []


def test_every_query_is_logged_with_caller_and_size(golden_graph, golden_dir, silent_log):
    eng = QueryEngine(golden_graph, log=silent_log, root=golden_dir)
    eng.find_symbol(name="query_users", caller="sibling-agent-7")
    served = [r for r in silent_log.records if r.get("event") == "query.served"]
    assert served, "a query must produce an audit record"
    rec = served[-1]
    assert rec["caller"] == "sibling-agent-7"
    assert "returned_nodes" in rec and "params" in rec
