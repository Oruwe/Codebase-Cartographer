"""Determinism: the same content must produce a byte-identical graph.

This is the guarantee that makes the graph diffable in CI. It is tested by
extracting twice, and by extracting from a copy at a different path with
different mtimes.
"""

import json
import os
import shutil
import time

from orgono.app.core.config import Config
from orgono.app.core.extract import extract_repo
from orgono.app.core.graph import Graph


def _strip_volatile(graph: Graph) -> dict:
    payload = graph.to_dict()
    payload.pop("stats", None)
    payload.pop("root", None)
    return payload


def test_same_repo_twice_is_byte_identical(golden_dir, silent_log):
    a = extract_repo(golden_dir, Config(), silent_log)
    b = extract_repo(golden_dir, Config(), silent_log)
    assert a.digest() == b.digest()
    assert json.dumps(_strip_volatile(a), sort_keys=True) == json.dumps(
        _strip_volatile(b), sort_keys=True
    )


def test_identical_content_at_a_different_path_and_mtime(golden_dir, silent_log, tmp_path):
    """A checkout changes mtimes and the path. Neither may change the graph."""
    copy = tmp_path / "elsewhere"
    shutil.copytree(golden_dir, copy)
    future = time.time() + 10_000
    for root, _dirs, files in os.walk(copy):
        for name in files:
            os.utime(os.path.join(root, name), (future, future))

    original = extract_repo(golden_dir, Config(), silent_log)
    moved = extract_repo(copy, Config(), silent_log)
    assert original.digest() == moved.digest()


def test_node_and_edge_ordering_is_total(golden_graph):
    nodes = golden_graph.sorted_nodes()
    assert [n.id for n in nodes] == sorted(n.id for n in nodes)
    edges = golden_graph.sorted_edges()
    keys = [(e.src, e.type, e.dst, e.path, e.line, e.col) for e in edges]
    assert keys == sorted(keys)


def test_round_trip_through_json_preserves_digest(golden_graph):
    restored = Graph.from_dict(json.loads(golden_graph.to_json()))
    assert restored.digest() == golden_graph.digest()
