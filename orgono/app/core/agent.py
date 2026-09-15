"""The agent: the tool surface Orgono exposes to sibling agents.

The tool surface is a fixed set of named operations with typed, structured
parameters. There is deliberately no "ask anything" entry point: a free-form
instruction from a sibling agent is an injection vector, and the capability that
would be abused is bulk export of this repository.

Every call is logged with its caller, its parameters and how much it returned.
"""

from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Config
from .egress import EgressPlan, EgressRefused, build_plan, execute, redacted_plan_for_display
from .extract import extract_repo, load_cache, save_cache
from .graph import Graph, summarize
from .obs import Logger, new_trace_id
from .query import QueryEngine, QueryRefused, QueryResult

DEFAULT_OUT_DIR = ".orgono"


def _self_ignore(out_dir: Path) -> None:
    """Make the output directory ignore itself.

    graph.json describes the private structure of someone's codebase. Writing a
    self-ignoring .gitignore inside our own directory keeps it out of commits
    without touching the user's .gitignore, which is theirs, not ours.
    """
    marker = out_dir / ".gitignore"
    if marker.exists():
        return
    # Never fail a run over a convenience file.
    with contextlib.suppress(OSError):
        marker.write_text("# Created by orgono. This directory is local-only.\n*\n", encoding="utf-8")

# The declared tool surface. Asserted against this module by tests/test_tool_surface.py
# so the contract cannot drift without a test failing.
TOOL_SURFACE: dict[str, dict[str, Any]] = {
    "find_symbol": {
        "description": "Find definitions by name, kind, language or path prefix.",
        "params": {
            "name": "str|None", "kind": "str|None", "language": "str|None",
            "path_prefix": "str|None", "exact": "bool", "include_snippets": "bool",
        },
    },
    "impact": {
        "description": "Which symbols reach, or are reached by, this symbol.",
        "params": {
            "symbol": "str", "direction": "callers|callees|both",
            "depth": "int", "include_snippets": "bool",
        },
    },
    "path_between": {
        "description": "The shortest typed edge path connecting two symbols.",
        "params": {"source": "str", "target": "str", "max_depth": "int"},
    },
    "file_summary": {
        "description": "Definitions and edges for a single file.",
        "params": {"path": "str"},
    },
    "stats": {
        "description": "Counts by node kind, edge type and language. No source content.",
        "params": {},
    },
}


@dataclass
class Cartographer:
    """Stateful façade: build or load a graph, then answer bounded questions."""

    root: Path
    config: Config
    log: Logger
    graph: Graph | None = None

    @staticmethod
    def create(
        root: str | Path = ".",
        config: Config | None = None,
        log: Logger | None = None,
    ) -> Cartographer:
        cfg = config or Config.from_env()
        logger = log or Logger(trace_id=new_trace_id())
        return Cartographer(root=Path(root).resolve(), config=cfg, log=logger)

    # -- lifecycle ------------------------------------------------------
    def out_dir(self) -> Path:
        return self.root / DEFAULT_OUT_DIR

    def graph_path(self) -> Path:
        return self.out_dir() / "graph.json"

    def cache_path(self) -> Path:
        return self.out_dir() / "cache" / "files.json"

    def build(self, use_cache: bool = True, write: bool = True) -> Graph:
        cache = load_cache(self.cache_path()) if use_cache else {}
        graph = extract_repo(self.root, self.config, self.log, cache=cache)
        self.graph = graph
        if write:
            self.write(graph)
        return graph

    def write(self, graph: Graph) -> Path:
        """Write the graph. Writes never escape the output directory."""
        out = self.out_dir()
        out.mkdir(parents=True, exist_ok=True)
        _self_ignore(out)
        target = self.graph_path()
        resolved_root = self.root.resolve()
        if not target.resolve().is_relative_to(resolved_root):
            raise PermissionError(f"refusing to write outside the repository: {target}")
        tmp = target.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(graph.to_json())
        tmp.replace(target)
        save_cache(self.cache_path(), graph)
        self.log.info("graph.written", path=str(target), digest=graph.digest())
        return target

    def load(self) -> Graph:
        path = self.graph_path()
        if not path.exists():
            raise FileNotFoundError(
                f"no graph at {path}. Run `orgono map` first."
            )
        self.graph = Graph.load(path)
        return self.graph

    def ensure_graph(self) -> Graph:
        if self.graph is not None:
            return self.graph
        try:
            return self.load()
        except FileNotFoundError:
            return self.build()

    def engine(self) -> QueryEngine:
        return QueryEngine(
            self.ensure_graph(), caps=self.config.caps, log=self.log, root=self.root
        )

    # -- tool surface ---------------------------------------------------
    def call_tool(self, tool: str, caller: str = "unknown", **params) -> dict:
        """Dispatch a structured tool call. Unknown tools are refused, not guessed."""
        if tool not in TOOL_SURFACE:
            raise QueryRefused(
                f"unknown tool {tool!r}. Available: {', '.join(sorted(TOOL_SURFACE))}"
            )
        allowed = set(TOOL_SURFACE[tool]["params"])
        extra = set(params) - allowed
        if extra:
            raise QueryRefused(
                f"{tool}: unexpected parameter(s) {sorted(extra)}; allowed: {sorted(allowed)}"
            )
        if tool == "stats":
            graph = self.ensure_graph()
            self.log.info("query.served", caller=caller, operation="stats", params={},
                          returned_nodes=0, returned_edges=0)
            return summarize(graph)
        engine = self.engine()
        method = getattr(engine, tool)
        result: QueryResult = method(caller=caller, **params)
        return result.to_dict()

    # -- optional egress ------------------------------------------------
    def explain(
        self,
        question: str,
        result: QueryResult,
        api_key: str | None = None,
        send: bool = False,
    ) -> dict:
        """Build (and only on explicit request, send) an OpenRouter payload."""
        policy = self.config.egress
        plan: EgressPlan = build_plan(
            question, result, self.ensure_graph(), policy, api_key=api_key, log=self.log
        )
        display = redacted_plan_for_display(plan)
        if not send:
            # No send requested: hand back the inspectable dry-run payload.
            return {"sent": False, "plan": display}
        # A send WAS requested. Refuse loudly rather than quietly downgrading to a
        # dry run -- a caller who asked to send and saw exit 0 would reasonably
        # believe the request went out.
        response = execute(plan, policy, api_key, log=self.log)
        return {"sent": True, "plan": display, "response": response}


def tool_manifest() -> str:
    """The stable, documented description of the tool surface."""
    return json.dumps(TOOL_SURFACE, indent=2, sort_keys=True)


__all__ = [
    "Cartographer",
    "TOOL_SURFACE",
    "tool_manifest",
    "QueryRefused",
    "EgressRefused",
]
