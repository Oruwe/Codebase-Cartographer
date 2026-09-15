"""A golden fixture with a known-correct graph.

The point of this file is that a regression in edge extraction becomes visible
rather than merely plausible. Every assertion below was checked by hand against
fixtures/golden_repo.
"""

from orgono.app.core.graph import summarize


def _names(graph, kind):
    return {n.name for n in graph.nodes.values() if n.kind == kind}


def _edge(graph, src_name, dst_name, etype):
    for e in graph.edges:
        s, d = graph.nodes.get(e.src), graph.nodes.get(e.dst)
        if s and d and s.name == src_name and d.name == dst_name and e.type == etype:
            return e
    return None


def test_python_definitions_are_found(golden_graph):
    assert {"connect", "query_users", "get_user_route", "health_route"} <= _names(golden_graph, "function")
    assert "UserTable" in _names(golden_graph, "class")
    assert "find_by_email" in _names(golden_graph, "method")


def test_javascript_and_go_definitions_are_found(golden_graph):
    assert {"renderUser", "format"} <= _names(golden_graph, "function")
    assert "Widget" in _names(golden_graph, "class")
    assert {"Service"} <= _names(golden_graph, "class")
    assert "Start" in _names(golden_graph, "method")


def test_cross_file_call_edge_exists_with_a_real_location(golden_graph):
    """api.get_user_route -> db.UserTable, across files."""
    edge = _edge(golden_graph, "get_user_route", "UserTable", "calls")
    assert edge is not None, "expected a cross-file call edge api.py -> db.py"
    assert edge.path == "src/api.py"
    assert edge.line == 6


def test_same_file_call_edge(golden_graph):
    edge = _edge(golden_graph, "find_by_email", "query_users", "calls")
    assert edge is not None
    assert edge.path == "src/db.py"
    assert edge.line == 11


def test_javascript_cross_file_call(golden_graph):
    edge = _edge(golden_graph, "renderUser", "format", "calls")
    assert edge is not None
    assert edge.path == "web/client.js"


def test_defines_edges_come_from_the_file_node(golden_graph):
    edge = _edge(golden_graph, "src/db.py", "query_users", "defines")
    assert edge is not None
    assert edge.line == 14


def test_import_edges(golden_graph):
    imports = {
        golden_graph.nodes[e.dst].name
        for e in golden_graph.edges
        if e.type == "imports" and e.dst in golden_graph.nodes
    }
    assert "sqlite3" in imports
    assert "src.db" in imports
    assert "./util.js" in imports


def test_broken_file_is_reported_not_dropped(golden_graph):
    report = golden_graph.files.get("src/broken.py")
    assert report is not None, "an unparseable file must never vanish"
    assert report.status in ("partial", "unparsed")
    assert report.reason


def test_unsupported_file_is_named_as_unsupported(golden_graph):
    report = golden_graph.files.get("data.csv")
    assert report is not None
    assert report.status == "unsupported"
    assert ".csv" in report.reason


def test_every_edge_carries_a_traceable_location(golden_graph):
    for e in golden_graph.edges:
        assert e.type in ("calls", "imports", "defines", "references")
        assert e.path, f"edge without a source path: {e}"
        assert e.line >= 1, f"edge without a line: {e}"


def test_summary_shape(golden_graph):
    s = summarize(golden_graph)
    assert s["nodes"] > 20
    assert set(s["languages"]) >= {"python", "javascript", "go"}
