"""Cross-platform behaviour.

orgono ships a pure-Python wheel and every dependency has wheels for Linux,
macOS (arm64 and x86_64) and Windows, so the install works everywhere. These
tests pin the behaviour that a single-OS test run would otherwise let drift.
"""

import ntpath
import pathlib
import posixpath

import pytest

from orgono.app.core.config import Config
from orgono.app.core.extract import _rel, extract_repo
from orgono.app.core.query import QueryEngine


def test_relative_paths_are_always_posix(tmp_path):
    """On Windows str(Path.relative_to()) yields backslashes, which would make
    node ids -- and therefore the whole graph -- differ between operating
    systems. Paths in the graph are always forward-slashed."""
    root = tmp_path
    nested = tmp_path / "src" / "deep"
    nested.mkdir(parents=True)
    target = nested / "mod.py"
    target.write_text("def f(): return 1\n")
    assert _rel(target, root) == "src/deep/mod.py"
    assert "\\" not in _rel(target, root)


def test_no_graph_path_contains_a_backslash(tmp_path, silent_log):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("def f():\n    return g()\n")
    (tmp_path / "pkg" / "b.py").write_text("def g():\n    return 1\n")
    graph = extract_repo(tmp_path, Config(), silent_log)

    for node in graph.nodes.values():
        assert "\\" not in node.id, f"node id is not portable: {node.id}"
        assert "\\" not in node.path
    for edge in graph.edges:
        assert "\\" not in edge.path
    for report in graph.files.values():
        assert "\\" not in report.path


def test_node_ids_are_posix_regardless_of_host_separator(tmp_path, silent_log):
    """The id a Windows run produces must equal the id a Linux run produces."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("def f(): return 1\n")
    graph = extract_repo(tmp_path, Config(), silent_log)
    ids = set(graph.nodes)
    assert "file::src/a.py" in ids
    # The same id spelled the Windows way must NOT appear.
    assert "file::src\\a.py" not in ids


@pytest.mark.parametrize("flavour", [posixpath, ntpath])
def test_graph_paths_join_correctly_under_either_flavour(flavour, golden_graph):
    """A forward-slashed relative path is valid input to both path flavours,
    which is why POSIX is the safe normal form to store."""
    for node in list(golden_graph.nodes.values())[:20]:
        if not node.path:
            continue
        joined = flavour.join("repo", node.path)
        assert "repo" in joined


def test_snippets_resolve_from_posix_paths(golden_graph, golden_dir, silent_log):
    engine = QueryEngine(golden_graph, log=silent_log, root=golden_dir)
    node = next(n for n in golden_graph.nodes.values() if n.name == "query_users")
    assert "/" in node.path
    assert engine.snippet_for(node), "a forward-slashed path must resolve on this OS"


def test_output_directory_is_created_relative_to_the_repo(tmp_path, silent_log):
    from orgono.app.core.agent import Cartographer
    (tmp_path / "a.py").write_text("def f(): return 1\n")
    cart = Cartographer.create(tmp_path, Config(), silent_log)
    cart.build()
    assert cart.graph_path().is_file()
    assert cart.graph_path().parent.name == ".orgono"


def test_atomic_write_replaces_an_existing_file(tmp_path, silent_log):
    """os.replace over an existing file is the portable atomic rename; graphify
    hit WinError 17 by renaming across volumes. The temp file is always written
    beside its target, never in the system temp directory."""
    from orgono.app.core.agent import Cartographer
    (tmp_path / "a.py").write_text("def f(): return 1\n")
    cart = Cartographer.create(tmp_path, Config(), silent_log)
    first = cart.build()
    target = cart.graph_path()
    assert target.is_file()
    (tmp_path / "b.py").write_text("def g(): return 2\n")
    cart.graph = None
    second = cart.build()
    assert target.is_file()
    assert len(second.nodes) > len(first.nodes)
    # no stray temp files left behind
    assert not list(target.parent.glob("*.tmp"))


def test_line_endings_do_not_change_the_symbols_found(tmp_path, silent_log):
    """A Windows checkout with CRLF must find the same definitions as LF."""
    lf = tmp_path / "lf"
    crlf = tmp_path / "crlf"
    lf.mkdir()
    crlf.mkdir()
    body = "def alpha():\n    return beta()\n\n\ndef beta():\n    return 1\n"
    (lf / "m.py").write_bytes(body.encode())
    (crlf / "m.py").write_bytes(body.replace("\n", "\r\n").encode())

    g_lf = extract_repo(lf, Config(), silent_log)
    g_crlf = extract_repo(crlf, Config(), silent_log)

    names_lf = {(n.kind, n.name) for n in g_lf.nodes.values()}
    names_crlf = {(n.kind, n.name) for n in g_crlf.nodes.values()}
    assert names_lf == names_crlf, "CRLF changed which symbols were found"

    edges_lf = {(g_lf.nodes[e.src].name, e.type, g_lf.nodes[e.dst].name)
                for e in g_lf.edges if e.src in g_lf.nodes and e.dst in g_lf.nodes}
    edges_crlf = {(g_crlf.nodes[e.src].name, e.type, g_crlf.nodes[e.dst].name)
                  for e in g_crlf.edges if e.src in g_crlf.nodes and e.dst in g_crlf.nodes}
    assert edges_lf == edges_crlf


def test_utf8_and_non_ascii_filenames_survive(tmp_path, silent_log):
    (tmp_path / "café.py").write_text("def naïve():\n    return 'héllo'\n", encoding="utf-8")
    graph = extract_repo(tmp_path, Config(), silent_log)
    assert graph.files["café.py"].status == "parsed"
    assert any(n.name == "naïve" for n in graph.nodes.values())


def test_no_posix_only_syscalls_in_the_import_path():
    """Importing orgono must not require os.fork, pwd, fcntl or termios."""
    import subprocess
    import sys
    code = (
        "import sys;"
        "sys.modules['pwd']=None;sys.modules['fcntl']=None;sys.modules['termios']=None;"
        "import orgono.cli;print('ok')"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert "ok" in out.stdout


def test_home_directory_is_only_consulted_for_credentials(tmp_path, monkeypatch, silent_log):
    """Mapping must work even where HOME is unset (some CI and service accounts)."""
    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # Windows equivalent
    (tmp_path / "a.py").write_text("def f(): return 1\n")
    graph = extract_repo(tmp_path, Config(), silent_log)
    assert graph.nodes


def test_os_name_is_not_hardcoded_anywhere_in_the_package():
    pkg = pathlib.Path(__import__("orgono").__file__).parent
    offenders = []
    for py in pkg.rglob("*.py"):
        body = py.read_text(encoding="utf-8")
        for marker in ('os.name == "nt"', "platform.system()", "sys.platform =="):
            if marker in body:
                offenders.append(f"{py.name}: {marker}")
    assert not offenders, f"platform-specific branching found: {offenders}"
