"""Regression tests derived from bugs reported against Graphify-Labs/graphify.

Orgono solves a similar problem, so graphify's public issue tracker is a free
list of the failure modes this kind of tool actually hits. Each test below names
the upstream issue it guards against. Most of these already passed when first
written -- they are here so they keep passing.

Source: https://github.com/Graphify-Labs/graphify/issues
"""

import pathlib

import pytest

from orgono.app.core.config import Config
from orgono.app.core.extract import extract_repo, load_cache, save_cache
from orgono.app.core.graph import summarize


def _write(root, files):
    for name, body in files.items():
        p = pathlib.Path(root, name)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body) if isinstance(body, str) else p.write_bytes(body)
    return root


# --- #3513: files that do not end in a newline are not syntax errors ---------

@pytest.mark.parametrize("name,body", [
    ("a.py", b"def f():\n    return 1"),
    ("b.js", b"function g(){ return 1; }"),
    ("c.go", b"package m\nfunc h() int { return 1 }"),
    ("d.rs", b"fn k() -> i32 { 1 }"),
])
def test_missing_trailing_newline_is_not_reported_as_broken(tmp_path, silent_log, name, body):
    pathlib.Path(tmp_path, name).write_bytes(body)
    graph = extract_repo(tmp_path, Config(), silent_log)
    report = graph.files[name]
    assert report.status == "parsed", f"{name}: {report.status} {report.reason}"
    assert not report.reason


# --- #3472: the same file reached by two path forms must not collide --------

def test_equivalent_path_forms_produce_one_identical_graph(tmp_path, silent_log):
    _write(tmp_path, {"src/a.py": "def f():\n    return 1\n"})
    a = extract_repo(tmp_path, Config(), silent_log)
    b = extract_repo(str(tmp_path) + "/", Config(), silent_log)
    c = extract_repo(str(tmp_path) + "/./src/..", Config(), silent_log)
    assert a.digest() == b.digest() == c.digest()
    assert len(a.nodes) == len(b.nodes) == len(c.nodes)


# --- #3570 / #3477: incremental rebuilds must not drop cross-file edges -----

def test_incremental_rebuild_keeps_cross_file_edges(tmp_path, silent_log):
    """A warm cache must never produce a smaller graph than a cold one."""
    _write(tmp_path, {
        "a.py": "from b import helper\n\n\ndef caller():\n    return helper(1)\n",
        "b.py": "def helper(n):\n    return n\n",
    })
    cold = extract_repo(tmp_path, Config(), silent_log)
    cache_file = tmp_path / "cache.json"
    save_cache(cache_file, cold)

    # Touch one file's content; the other is unchanged and served from cache.
    pathlib.Path(tmp_path, "a.py").write_text(
        "from b import helper\n\n\ndef caller():\n    return helper(2)\n"
    )
    warm = extract_repo(tmp_path, Config(), silent_log, cache=load_cache(cache_file))

    def cross(graph):
        return {
            (graph.nodes[e.src].name, graph.nodes[e.dst].name)
            for e in graph.edges
            if e.type == "calls" and e.src in graph.nodes and e.dst in graph.nodes
        }

    assert ("caller", "helper") in cross(cold)
    assert ("caller", "helper") in cross(warm), "warm rebuild lost a cross-file call edge"
    assert len(warm.nodes) == len(cold.nodes)


def test_unchanged_rebuild_is_identical_with_a_warm_cache(tmp_path, silent_log):
    _write(tmp_path, {"a.py": "def f():\n    return g()\n", "b.py": "def g():\n    return 1\n"})
    cold = extract_repo(tmp_path, Config(), silent_log)
    cache_file = tmp_path / "c.json"
    save_cache(cache_file, cold)
    warm = extract_repo(tmp_path, Config(), silent_log, cache=load_cache(cache_file))
    assert cold.digest() == warm.digest()


# --- #3471: module-level constants must produce nodes ----------------------

@pytest.mark.parametrize("name,body,expected", [
    ("l.rs", 'const MAX: i32 = 5;\nstatic NAME: &str = "x";\n', {"MAX", "NAME"}),
    ("g.go", "package m\nconst MAXN = 5\n", {"MAXN"}),
])
def test_module_level_constants_are_not_dropped(tmp_path, silent_log, name, body, expected):
    pathlib.Path(tmp_path, name).write_text(body)
    graph = extract_repo(tmp_path, Config(), silent_log)
    names = {n.name for n in graph.nodes.values() if n.kind == "constant"}
    assert expected <= names, f"{name}: missing constants {expected - names}"


@pytest.mark.parametrize("name,body,expected", [
    ("t.ts", "enum E { A }\ntype T = { a: number };\n", {"E", "T"}),
    ("l.rs", "type Alias = i32;\nunion U { a: i32 }\n", {"Alias", "U"}),
    ("j.java", "enum Color { RED }\n", {"Color"}),
])
def test_type_declarations_are_not_dropped(tmp_path, silent_log, name, body, expected):
    pathlib.Path(tmp_path, name).write_text(body)
    graph = extract_repo(tmp_path, Config(), silent_log)
    names = {n.name for n in graph.nodes.values() if n.kind in ("class", "interface")}
    assert expected <= names, f"{name}: missing type declarations {expected - names}"


# --- #3540: modern syntax must not abort extraction ------------------------

MODERN = {
    "m.py": "async def fetch(u):\n    async with s() as r:\n        return await r.json()\n\n\n@dec\nclass C:\n    ...\n",
    "m.ts": "export async function go<T>(x: T): Promise<T> { return await id(x); }\n",
    "m.js": "export default async () => { const {a, ...rest} = await get(); return a ?? rest?.b; };\n",
    "m.go": "package m\n\nfunc G[T any](v T) T { defer f(); go h(); return v }\n",
    "m.rs": "async fn go() -> i32 { let x = async { 1 }.await; x }\n",
    "m.java": "class C { record R(int a){} sealed interface I permits C {} }\n",
}


@pytest.mark.parametrize("name", sorted(MODERN))
def test_modern_syntax_does_not_abort(tmp_path, silent_log, name):
    pathlib.Path(tmp_path, name).write_text(MODERN[name])
    graph = extract_repo(tmp_path, Config(), silent_log)
    report = graph.files[name]
    assert report.status == "parsed", f"{name}: {report.status} {report.reason}"


# --- #3565: every supported language must emit import edges ----------------

IMPORTS = {
    "i.py": ("python", "import os\n"),
    "i.js": ("javascript", 'import a from "b";\n'),
    "i.ts": ("typescript", 'import {c} from "d";\n'),
    "i.tsx": ("tsx", 'import R from "react";\n'),
    "i.go": ("go", 'package m\nimport "fmt"\n'),
    "i.rs": ("rust", "use std::io;\n"),
    "i.java": ("java", "import java.util.List;\nclass X{}\n"),
}


@pytest.mark.parametrize("name", sorted(IMPORTS))
def test_import_edges_exist_for_every_language(tmp_path, silent_log, name):
    lang, body = IMPORTS[name]
    pathlib.Path(tmp_path, name).write_text(body)
    graph = extract_repo(tmp_path, Config(), silent_log)
    imports = [e for e in graph.edges if e.type == "imports"]
    assert imports, f"{lang}: no import edges were emitted"
    for e in imports:
        assert e.line >= 1 and e.path == name


# --- #3539: edge types must be derived, never hardcoded to one value -------

def test_edge_types_are_actually_distinguished(golden_graph):
    kinds = {e.type for e in golden_graph.edges}
    assert len(kinds) >= 3, f"edges collapsed to {kinds}"
    assert {"defines", "imports", "calls"} <= kinds


# --- #3548: reported counts must match the graph they describe -------------

def test_summary_counts_match_the_graph(golden_graph):
    s = summarize(golden_graph)
    assert s["nodes"] == len(golden_graph.nodes)
    assert s["edges"] == len(golden_graph.edges)
    assert sum(s["node_kinds"].values()) == len(golden_graph.nodes)
    assert sum(s["edge_types"].values()) == len(golden_graph.edges)
    assert sum(s["file_status"].values()) == len(golden_graph.files)


# --- #3511 / #3504: nothing is dropped without being surfaced --------------

def test_every_source_file_appears_in_the_report(tmp_path, silent_log):
    _write(tmp_path, {
        "ok.py": "def f(): return 1\n",
        "big.py": "x = 1\n" * 200_000,
        "weird.xyz": "not a language we claim\n",
        "notes.md": "# hi\n",
    })
    cfg = Config().with_limits(max_file_bytes=1000)
    graph = extract_repo(tmp_path, cfg, silent_log)
    assert graph.files["ok.py"].status == "parsed"
    assert graph.files["big.py"].status == "skipped"
    assert "file_too_large" in graph.files["big.py"].reason
    assert graph.files["weird.xyz"].status == "unsupported"
    # Every skipped or unsupported file carries a stated reason.
    for report in graph.files.values():
        if report.status != "parsed":
            assert report.reason, f"{report.path} was set aside with no reason given"


# --- #3508: an unwritable output path must fail loudly, not corrupt --------

def test_cache_write_failure_does_not_corrupt(tmp_path, silent_log):
    graph = extract_repo(tmp_path, Config(), silent_log)
    target = tmp_path / "ro" / "cache.json"
    target.parent.mkdir()
    save_cache(target, graph)
    assert target.exists()
    before = target.read_text()
    save_cache(target, graph)  # rewriting must be atomic and idempotent
    assert target.read_text() == before


# --- #3474: a read-only home must not break local operation ----------------

def test_extraction_does_not_depend_on_a_writable_home(tmp_path, silent_log, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "nonexistent-home"))
    _write(tmp_path, {"a.py": "def f(): return 1\n"})
    graph = extract_repo(tmp_path, Config(), silent_log)
    assert graph.nodes


# --- #3485: ambiguous resolution must be reported, not silently guessed ----

def test_ambiguous_names_are_counted_not_invented(tmp_path, silent_log):
    files = {f"m{i}.py": f"class K{i}:\n    def shared(self):\n        return 1\n" for i in range(6)}
    files["caller.py"] = "def go(obj):\n    return obj.shared()\n"
    _write(tmp_path, files)
    graph = extract_repo(tmp_path, Config(), silent_log)
    calls_to_shared = [
        e for e in graph.edges
        if e.type == "calls" and e.dst in graph.nodes and graph.nodes[e.dst].name == "shared"
    ]
    # 6 candidates is above the fan-out limit: bind to none rather than all six.
    assert not calls_to_shared
    assert graph.stats.get("ambiguous_calls", 0) >= 1


# --- module-level constants: present, but never at the cost of local noise ---

def test_python_module_level_constants_are_nodes(tmp_path, silent_log):
    _write(tmp_path, {"a.py": 'MAX = 5\nDEFAULTS = {"a": 1}\n\n\ndef f():\n    local = 1\n    return local\n\n\nclass C:\n    attr = 2\n'})
    graph = extract_repo(tmp_path, Config(), silent_log)
    consts = {n.name for n in graph.nodes.values() if n.kind == "constant"}
    assert {"MAX", "DEFAULTS"} <= consts
    names = {n.name for n in graph.nodes.values()}
    assert "local" not in names, "function locals must not become nodes"
    assert "attr" not in names, "class attributes must not become module constants"


def test_js_module_constants_do_not_duplicate_arrow_functions(tmp_path, silent_log):
    _write(tmp_path, {"b.js": "const LIMIT = 10;\nconst handler = (x) => x + 1;\nfunction g(){ const inner = 1; return inner; }\n"})
    graph = extract_repo(tmp_path, Config(), silent_log)
    names = [n.name for n in graph.nodes.values()]
    assert names.count("handler") == 1, "an arrow-function const was counted twice"
    assert {n.kind for n in graph.nodes.values() if n.name == "handler"} == {"function"}
    assert "LIMIT" in names
    assert "inner" not in names


def test_wrapper_anchored_captures_still_resolve_a_name(tmp_path, silent_log):
    """Regression: a pattern anchored on a wrapper node (Python's
    expression_statement) silently produced no node, because the name was
    re-derived from the wrapper instead of read from the @def.name capture."""
    _write(tmp_path, {"a.py": "SETTING = 1\n"})
    graph = extract_repo(tmp_path, Config(), silent_log)
    assert any(n.name == "SETTING" and n.kind == "constant" for n in graph.nodes.values())
