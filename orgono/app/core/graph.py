"""The knowledge graph: nodes, typed edges, and byte-identical serialization.

Determinism is a guarantee here, not an aspiration: every collection is sorted
before it is written, and nothing derived from dict insertion order or
filesystem order is allowed to reach the output. tests/test_determinism.py
extracts the same tree twice and byte-compares.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field

SCHEMA_VERSION = "orgono-graph/1"

NODE_KINDS = (
    "file",
    "function",
    "method",
    "class",
    "interface",
    "constant",
    "external",
    "unparsed",
)

EDGE_TYPES = ("calls", "imports", "defines", "references")


@dataclass(frozen=True, order=True)
class Node:
    id: str
    kind: str
    name: str
    path: str
    language: str = ""
    start_line: int = 0
    end_line: int = 0
    # Populated only for `unparsed` nodes: why the file could not be parsed.
    error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, order=True)
class Edge:
    """A typed edge. Every edge carries its source location so any answer in the
    query surface can be traced back to a line of code."""

    src: str
    dst: str
    type: str
    path: str
    line: int
    col: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class FileReport:
    """Per-file outcome. An unparseable file is recorded, never silently dropped."""

    path: str
    status: str  # parsed | partial | unparsed | skipped | unsupported
    language: str = ""
    reason: str = ""
    bytes: int = 0
    sha256: str = ""
    nodes: int = 0
    edges: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Graph:
    root: str = ""
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: set[Edge] = field(default_factory=set)
    files: dict[str, FileReport] = field(default_factory=dict)
    stats: dict = field(default_factory=dict)

    # -- mutation -------------------------------------------------------
    def add_node(self, node: Node) -> Node:
        existing = self.nodes.get(node.id)
        if existing is not None:
            return existing
        self.nodes[node.id] = node
        return node

    def add_edge(self, edge: Edge) -> None:
        if edge.type not in EDGE_TYPES:
            raise ValueError(f"unknown edge type: {edge.type}")
        self.edges.add(edge)

    # -- views ----------------------------------------------------------
    def sorted_nodes(self) -> list[Node]:
        return sorted(self.nodes.values(), key=lambda n: n.id)

    def sorted_edges(self) -> list[Edge]:
        return sorted(self.edges, key=lambda e: (e.src, e.type, e.dst, e.path, e.line, e.col))

    def sorted_files(self) -> list[FileReport]:
        return sorted(self.files.values(), key=lambda f: f.path)

    def out_edges(self, node_id: str) -> list[Edge]:
        return [e for e in self.sorted_edges() if e.src == node_id]

    def in_edges(self, node_id: str) -> list[Edge]:
        return [e for e in self.sorted_edges() if e.dst == node_id]

    def neighbors(self, node_id: str, direction: str = "both") -> list[str]:
        out: set[str] = set()
        for e in self.edges:
            if direction in ("out", "both") and e.src == node_id:
                out.add(e.dst)
            if direction in ("in", "both") and e.dst == node_id:
                out.add(e.src)
        return sorted(out)

    # -- serialization --------------------------------------------------
    def to_dict(self) -> dict:
        """A fully sorted, deterministic dict. Same input -> identical bytes."""
        return {
            "schema_version": SCHEMA_VERSION,
            "root": self.root,
            "nodes": [n.to_dict() for n in self.sorted_nodes()],
            "edges": [e.to_dict() for e in self.sorted_edges()],
            "files": [f.to_dict() for f in self.sorted_files()],
            "stats": {k: self.stats[k] for k in sorted(self.stats)},
        }

    def to_json(self, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True, ensure_ascii=False)

    def digest(self) -> str:
        """Content digest of the graph, excluding volatile stats (timings)."""
        payload = self.to_dict()
        payload.pop("stats", None)
        payload.pop("root", None)
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
        return hashlib.sha256(blob).hexdigest()

    @staticmethod
    def from_dict(data: dict) -> Graph:
        g = Graph(root=data.get("root", ""))
        for raw in data.get("nodes", []):
            g.nodes[raw["id"]] = Node(**raw)
        for raw in data.get("edges", []):
            g.edges.add(Edge(**raw))
        for raw in data.get("files", []):
            g.files[raw["path"]] = FileReport(**raw)
        g.stats = dict(data.get("stats", {}))
        return g

    @staticmethod
    def load(path) -> Graph:
        with open(path, encoding="utf-8") as fh:
            return Graph.from_dict(json.load(fh))


def make_node_id(path: str, kind: str, name: str, line: int) -> str:
    """Stable, human-readable, collision-resistant node id."""
    if kind == "file":
        return f"file::{path}"
    if kind == "external":
        return f"external::{name}"
    return f"{path}::{kind}:{name}@{line}"


def summarize(graph: Graph) -> dict:
    """Counts by node kind and edge type. Sorted; safe to print."""
    kinds: dict[str, int] = {}
    for n in graph.nodes.values():
        kinds[n.kind] = kinds.get(n.kind, 0) + 1
    types: dict[str, int] = {}
    for e in graph.edges:
        types[e.type] = types.get(e.type, 0) + 1
    langs: dict[str, int] = {}
    statuses: dict[str, int] = {}
    for f in graph.files.values():
        statuses[f.status] = statuses.get(f.status, 0) + 1
        if f.language:
            langs[f.language] = langs.get(f.language, 0) + 1
    return {
        "nodes": len(graph.nodes),
        "edges": len(graph.edges),
        "node_kinds": {k: kinds[k] for k in sorted(kinds)},
        "edge_types": {k: types[k] for k in sorted(types)},
        "languages": {k: langs[k] for k in sorted(langs)},
        "file_status": {k: statuses[k] for k in sorted(statuses)},
    }


def iter_ids(nodes: Iterable[Node]) -> list[str]:
    return sorted(n.id for n in nodes)
