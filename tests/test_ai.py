"""The AI layer. Retrieval is deterministic code; the model is optional."""

import pytest

from orgono.app.core.ai import (
    extract_terms,
    format_models,
    merge_results,
    models_url,
    parse_models_response,
    prune,
    retrieve,
    structural_answer,
)
from orgono.app.core.query import QueryEngine, QueryResult


@pytest.fixture
def engine(golden_graph, golden_dir, silent_log):
    return QueryEngine(golden_graph, log=silent_log, root=golden_dir)


def test_terms_are_only_symbols_that_exist(golden_graph):
    terms = extract_terms("how does query_users reach connect?", golden_graph)
    assert "query_users" in terms
    assert "connect" in terms


def test_stopwords_and_unknown_words_are_ignored(golden_graph):
    terms = extract_terms("what happens when the codebase explodes entirely", golden_graph)
    assert terms == []


def test_retrieval_is_deterministic(engine):
    a = retrieve("how does query_users reach connect?", engine)
    b = retrieve("how does query_users reach connect?", engine)
    assert a.terms == b.terms
    assert a.citations == b.citations


def test_retrieval_finds_the_connecting_path(engine):
    grounding = retrieve("how does get_user_route reach query_users?", engine, depth=3)
    paths = [r for r in grounding.results if r.operation == "path_between"]
    assert paths and paths[0].edges


def test_structural_answer_works_with_no_model(engine):
    grounding = retrieve("what calls query_users?", engine)
    answer = structural_answer(grounding)
    assert "query_users" in answer
    assert "src/db.py" in answer


def test_answer_for_an_unknown_symbol_is_honest(engine):
    grounding = retrieve("explain the flux capacitor subsystem", engine)
    answer = structural_answer(grounding)
    assert "no symbol" in answer.lower()


def test_prune_prefers_definitions_over_externals():
    """Regression: retrieval used to be swamped by builtins like `append`."""
    res = QueryResult(operation="impact")
    res.nodes = [
        {"id": f"external::ext{i}", "kind": "external", "name": f"ext{i}", "path": "",
         "start_line": 0, "end_line": 0, "language": ""} for i in range(50)
    ] + [
        {"id": "a.py::function:real@1", "kind": "function", "name": "real", "path": "a.py",
         "start_line": 1, "end_line": 2, "language": "python"}
    ]
    pruned = prune(res, max_nodes=10, max_external=3)
    kinds = [n["kind"] for n in pruned.nodes]
    assert kinds.count("external") <= 3
    assert any(k == "function" for k in kinds)


def test_merge_results_respects_caps(engine):
    grounding = retrieve("what calls query_users?", engine)
    merged = merge_results(grounding, max_nodes=2, max_edges=2)
    assert len(merged.nodes) <= 2
    assert len(merged.edges) <= 2
    assert set(merged.snippets) <= {n["id"] for n in merged.nodes}


# --- OpenRouter model listing ------------------------------------------------
# openrouter.ai is unreachable from the build sandbox, so this is tested against
# a recorded fixture of the documented response shape, not a live call.

FIXTURE = {
    "data": [
        {"id": "z/model-b", "name": "B", "context_length": 8192,
         "pricing": {"prompt": "0.000001", "completion": "0.000002"}},
        {"id": "a/model-free", "name": "Free", "context_length": 32768,
         "pricing": {"prompt": "0", "completion": "0"}},
        {"not_a_model": True},
    ]
}


def test_models_response_is_parsed_and_sorted():
    rows = parse_models_response(FIXTURE)
    assert [r["id"] for r in rows] == ["a/model-free", "z/model-b"]
    assert rows[0]["free"] is True
    assert rows[1]["free"] is False


def test_malformed_model_rows_are_skipped():
    assert parse_models_response({"data": [{}, None, {"id": ""}]}) == []
    assert parse_models_response({}) == []


def test_free_filter_and_formatting():
    rows = parse_models_response(FIXTURE)
    out = format_models(rows, only_free=True)
    assert "a/model-free" in out
    assert "z/model-b" not in out


def test_models_url_is_built_from_base_url():
    assert models_url("https://openrouter.ai/api/v1/") == "https://openrouter.ai/api/v1/models"
