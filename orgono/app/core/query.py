"""The query surface. Every caller is treated as untrusted.

A sibling agent can be prompt-injected, and the thing it will be injected to do
is exfiltrate this repository. So the surface here takes structured parameters
rather than free-form instructions, every response is capped by code, and a
query whose selector would sweep the whole graph is refused rather than
truncated. "Summarise the entire codebase" is an exfiltration request wearing a
reasonable hat.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .config import QueryCaps
from .graph import Edge, Graph, Node
from .obs import NULL_LOGGER, Logger
from .redact import redact_text

VALID_KINDS = ("file", "function", "method", "class", "interface", "constant", "external", "unparsed")
VALID_EDGE_TYPES = ("calls", "imports", "defines", "references")
VALID_DIRECTIONS = ("callers", "callees", "both")


class QueryRefused(PermissionError):
    """Raised when a query is refused by policy rather than failing technically."""


@dataclass
class QueryResult:
    operation: str
    nodes: list[dict] = field(default_factory=list)
    edges: list[dict] = field(default_factory=list)
    snippets: dict[str, list[str]] = field(default_factory=dict)
    truncated: bool = False
    total_matched: int = 0
    notes: list[str] = field(default_factory=list)
    # How many secrets were redacted while building this result. Counted where
    # the redaction happens, so the dry-run payload reports the true number.
    redactions: int = 0

    def to_dict(self) -> dict:
        return {
            "operation": self.operation,
            "nodes": self.nodes,
            "edges": self.edges,
            "snippets": self.snippets,
            "truncated": self.truncated,
            "total_matched": self.total_matched,
            "notes": self.notes,
            "redactions": self.redactions,
        }


def _node_dict(n: Node) -> dict:
    return {
        "id": n.id,
        "kind": n.kind,
        "name": n.name,
        "path": n.path,
        "language": n.language,
        "start_line": n.start_line,
        "end_line": n.end_line,
    }


def _edge_dict(e: Edge) -> dict:
    return {"src": e.src, "dst": e.dst, "type": e.type, "path": e.path, "line": e.line, "col": e.col}


class QueryEngine:
    """Structured, capped, logged access to a graph."""

    def __init__(
        self,
        graph: Graph,
        caps: QueryCaps | None = None,
        log: Logger | None = None,
        root: str | Path | None = None,
    ) -> None:
        self.graph = graph
        self.caps = caps or QueryCaps()
        self.log = log or NULL_LOGGER
        self.root = Path(root or graph.root or ".")

    # -- policy ---------------------------------------------------------
    def _check_not_bulk(self, matched: int, operation: str, selector_given: bool) -> None:
        total = max(len(self.graph.nodes), 1)
        if not selector_given:
            raise QueryRefused(
                f"{operation}: refused - no selector given. This surface does not support "
                "whole-graph export; supply a name, path, kind or symbol."
            )
        if total >= self.caps.bulk_refusal_min_nodes:
            ratio = matched / total
            if ratio > self.caps.bulk_refusal_ratio:
                raise QueryRefused(
                    f"{operation}: refused - selector matches {matched}/{total} nodes "
                    f"({ratio:.0%} of the graph), above the {self.caps.bulk_refusal_ratio:.0%} "
                    "bulk-export threshold. Narrow the query."
                )

    def _clamp_depth(self, depth: int, result: QueryResult) -> int:
        if depth > self.caps.max_depth:
            result.notes.append(f"depth clamped from {depth} to {self.caps.max_depth}")
            return self.caps.max_depth
        return max(1, depth)

    def _finish(self, result: QueryResult, caller: str, params: dict) -> QueryResult:
        if len(result.nodes) > self.caps.max_nodes:
            result.nodes = result.nodes[: self.caps.max_nodes]
            result.truncated = True
            result.notes.append(f"nodes truncated to cap {self.caps.max_nodes}")
        if len(result.edges) > self.caps.max_edges:
            result.edges = result.edges[: self.caps.max_edges]
            result.truncated = True
            result.notes.append(f"edges truncated to cap {self.caps.max_edges}")
        self.log.info(
            "query.served",
            caller=caller,
            operation=result.operation,
            params=params,
            returned_nodes=len(result.nodes),
            returned_edges=len(result.edges),
            total_matched=result.total_matched,
            truncated=result.truncated,
        )
        return result

    # -- snippets -------------------------------------------------------
    def snippet_for(self, node: Node, counter: list[int] | None = None) -> list[str]:
        """Read at most `max_snippet_lines` lines for a node, redacted.

        Reads are confined to the graph root; a node whose path escapes it
        returns nothing rather than reading an arbitrary file.
        """
        if not node.path or node.kind == "external":
            return []
        try:
            root = self.root.resolve()
            target = (root / node.path).resolve()
            if not target.is_relative_to(root) or not target.is_file():
                return []
            with open(target, encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
        except OSError:
            return []
        start = max(node.start_line - 1, 0)
        end = min(node.end_line or node.start_line, start + self.caps.max_snippet_lines)
        end = max(end, start + 1)
        chunk = lines[start : min(end, start + self.caps.max_snippet_lines)]
        out: list[str] = []
        for line in chunk:
            text, n = redact_text(line.rstrip("\n"))
            if counter is not None:
                counter[0] += n
            out.append(text)
        return out

    def _attach_snippets(self, result: QueryResult, node_ids: list[str], want: bool) -> None:
        if not want:
            return
        counter = [0]
        for nid in node_ids[: self.caps.max_nodes]:
            node = self.graph.nodes.get(nid)
            if node is None:
                continue
            lines = self.snippet_for(node, counter=counter)
            if lines:
                result.snippets[nid] = lines
        result.redactions += counter[0]

    # -- operations -----------------------------------------------------
    def find_symbol(
        self,
        name: str | None = None,
        kind: str | None = None,
        language: str | None = None,
        path_prefix: str | None = None,
        exact: bool = False,
        include_snippets: bool = False,
        caller: str = "unknown",
    ) -> QueryResult:
        params = {
            "name": name, "kind": kind, "language": language,
            "path_prefix": path_prefix, "exact": exact,
        }
        if kind is not None and kind not in VALID_KINDS:
            raise ValueError(f"invalid kind {kind!r}; expected one of {VALID_KINDS}")
        result = QueryResult(operation="find_symbol")
        selector_given = any(v for v in (name, kind, language, path_prefix))

        matches: list[Node] = []
        needle = (name or "").lower()
        for node in self.graph.sorted_nodes():
            if name:
                if exact:
                    if node.name != name:
                        continue
                elif needle not in node.name.lower():
                    continue
            if kind and node.kind != kind:
                continue
            if language and node.language != language:
                continue
            if path_prefix and not node.path.startswith(path_prefix):
                continue
            matches.append(node)

        self._check_not_bulk(len(matches), "find_symbol", selector_given)
        result.total_matched = len(matches)
        result.nodes = [_node_dict(n) for n in matches[: self.caps.max_nodes]]
        if len(matches) > self.caps.max_nodes:
            result.truncated = True
        self._attach_snippets(result, [n.id for n in matches], include_snippets)
        return self._finish(result, caller, params)

    def impact(
        self,
        symbol: str,
        direction: str = "callers",
        depth: int = 2,
        include_snippets: bool = False,
        caller: str = "unknown",
    ) -> QueryResult:
        """Impact analysis: what reaches, or is reached by, this symbol."""
        params = {"symbol": symbol, "direction": direction, "depth": depth}
        if direction not in VALID_DIRECTIONS:
            raise ValueError(f"invalid direction {direction!r}; expected one of {VALID_DIRECTIONS}")
        if not symbol or not symbol.strip():
            raise QueryRefused("impact: refused - a symbol is required.")
        result = QueryResult(operation="impact")
        depth = self._clamp_depth(depth, result)

        seeds = [n for n in self.graph.sorted_nodes() if n.name == symbol or n.id == symbol]
        if not seeds:
            result.notes.append(f"no symbol named {symbol!r} in the graph")
            return self._finish(result, caller, params)

        want_in = direction in ("callers", "both")
        want_out = direction in ("callees", "both")

        frontier = {n.id for n in seeds}
        seen = set(frontier)
        collected_edges: list[Edge] = []
        for _ in range(depth):
            nxt: set[str] = set()
            for edge in self.graph.sorted_edges():
                if want_in and edge.dst in frontier and edge.src not in seen:
                    collected_edges.append(edge)
                    nxt.add(edge.src)
                elif want_in and edge.dst in frontier:
                    collected_edges.append(edge)
                if want_out and edge.src in frontier and edge.dst not in seen:
                    collected_edges.append(edge)
                    nxt.add(edge.dst)
                elif want_out and edge.src in frontier:
                    collected_edges.append(edge)
            seen |= nxt
            frontier = nxt
            if not frontier:
                break

        nodes = [self.graph.nodes[i] for i in sorted(seen) if i in self.graph.nodes]
        self._check_not_bulk(len(nodes), "impact", selector_given=True)
        result.total_matched = len(nodes)
        result.nodes = [_node_dict(n) for n in nodes[: self.caps.max_nodes]]
        uniq = sorted({(e.src, e.type, e.dst, e.path, e.line, e.col) for e in collected_edges})
        result.edges = [
            {"src": s, "type": t, "dst": d, "path": p, "line": ln, "col": c}
            for (s, t, d, p, ln, c) in uniq
        ]
        self._attach_snippets(result, [n.id for n in nodes], include_snippets)
        return self._finish(result, caller, params)

    def path_between(
        self,
        source: str,
        target: str,
        max_depth: int = 5,
        caller: str = "unknown",
    ) -> QueryResult:
        """What connects A to B - the question the whole agent exists to answer."""
        params = {"source": source, "target": target, "max_depth": max_depth}
        result = QueryResult(operation="path_between")
        if not source.strip() or not target.strip():
            raise QueryRefused("path_between: refused - both source and target are required.")
        max_depth = self._clamp_depth(max_depth, result)

        starts = sorted(n.id for n in self.graph.sorted_nodes() if source in (n.name, n.id))
        ends = {n.id for n in self.graph.sorted_nodes() if target in (n.name, n.id)}
        if not starts or not ends:
            result.notes.append("source or target not present in graph")
            return self._finish(result, caller, params)

        adjacency: dict[str, list[Edge]] = {}
        for e in self.graph.sorted_edges():
            adjacency.setdefault(e.src, []).append(e)

        best: list[Edge] | None = None
        for start in starts:
            queue: list[tuple[str, list[Edge]]] = [(start, [])]
            visited = {start}
            while queue:
                current, trail = queue.pop(0)
                if len(trail) > max_depth:
                    continue
                if current in ends and trail:
                    if best is None or len(trail) < len(best):
                        best = trail
                    break
                for edge in adjacency.get(current, []):
                    if edge.dst not in visited:
                        visited.add(edge.dst)
                        queue.append((edge.dst, [*trail, edge]))
            if best and len(best) <= 1:
                break

        if best is None:
            result.notes.append(f"no path from {source!r} to {target!r} within depth {max_depth}")
            return self._finish(result, caller, params)

        ids: list[str] = []
        for e in best:
            for nid in (e.src, e.dst):
                if nid not in ids:
                    ids.append(nid)
        result.nodes = [_node_dict(self.graph.nodes[i]) for i in ids if i in self.graph.nodes]
        result.edges = [_edge_dict(e) for e in best]
        result.total_matched = len(ids)
        return self._finish(result, caller, params)

    def file_summary(self, path: str, caller: str = "unknown") -> QueryResult:
        params = {"path": path}
        result = QueryResult(operation="file_summary")
        if not path.strip():
            raise QueryRefused("file_summary: refused - a path is required.")
        nodes = [n for n in self.graph.sorted_nodes() if n.path == path]
        if not nodes:
            result.notes.append(f"no such file in graph: {path}")
            return self._finish(result, caller, params)
        result.total_matched = len(nodes)
        result.nodes = [_node_dict(n) for n in nodes[: self.caps.max_nodes]]
        ids = {n.id for n in nodes}
        result.edges = [
            _edge_dict(e) for e in self.graph.sorted_edges() if e.src in ids or e.dst in ids
        ][: self.caps.max_edges]
        report = self.graph.files.get(path)
        if report:
            result.notes.append(f"status={report.status} language={report.language} {report.reason}".strip())
        return self._finish(result, caller, params)
