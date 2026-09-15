"""The AI layer: grounded question answering over the graph.

Design rule, taken seriously: the model never decides anything a guarantee
depends on. Retrieval is deterministic Python over the graph (and is unit
tested); the model only turns retrieved, redacted, cited context into prose.
If the model is unavailable, `retrieve()` alone still answers structurally.

Any OpenRouter model id works - there is no allowlist. `orgono models` lists
what the account can reach.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .graph import Graph
from .obs import NULL_LOGGER, Logger
from .query import QueryEngine, QueryResult

# Identifier-ish tokens in a question, longest first so `redact_text` beats `text`.
_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")

_STOPWORDS = {
    "the", "and", "for", "what", "which", "where", "how", "does", "did", "why",
    "this", "that", "with", "from", "into", "when", "who", "are", "was", "were",
    "can", "could", "would", "should", "about", "show", "tell", "give", "find",
    "explain", "code", "codebase", "repo", "repository", "file", "files",
    "function", "functions", "class", "classes", "method", "methods", "call",
    "calls", "use", "used", "uses", "using", "connect", "connects", "connected",
    "between", "impact", "affect", "affects", "happens", "happen", "there",
    "get", "set", "all", "any", "not", "but", "you", "your", "its", "have",
}


@dataclass
class Grounding:
    """Everything retrieved for a question, with provenance."""

    question: str
    terms: list[str] = field(default_factory=list)
    results: list[QueryResult] = field(default_factory=list)
    citations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "question": self.question,
            "terms": self.terms,
            "results": [r.to_dict() for r in self.results],
            "citations": self.citations,
        }

    def is_empty(self) -> bool:
        return not any(r.nodes or r.edges for r in self.results)


# Ranking is deliberately deterministic: a real definition always outranks an
# `external` stub. Without this, retrieval for a well-connected symbol is
# swamped by builtins (`append`, `any`, `Path`) and the token budget is spent on
# noise instead of code. Found by running retrieval, not by reading it.
_KIND_RANK = {
    "function": 0, "method": 0, "class": 1, "interface": 1,
    "constant": 2, "file": 2, "unparsed": 3, "external": 9,
}


def _rank_key(node: dict) -> tuple:
    return (_KIND_RANK.get(node.get("kind", ""), 5), node.get("path") == "", node.get("id", ""))


def prune(result: QueryResult, max_nodes: int = 40, max_external: int = 6) -> QueryResult:
    """Keep the highest-signal nodes and the edges that still connect them."""
    ranked = sorted(result.nodes, key=_rank_key)
    kept: list[dict] = []
    externals = 0
    for node in ranked:
        if node.get("kind") == "external":
            if externals >= max_external:
                continue
            externals += 1
        kept.append(node)
        if len(kept) >= max_nodes:
            break
    keep_ids = {n["id"] for n in kept}
    result.nodes = sorted(kept, key=lambda n: n["id"])
    result.edges = [
        e for e in result.edges if e["src"] in keep_ids and e["dst"] in keep_ids
    ]
    result.snippets = {k: v for k, v in result.snippets.items() if k in keep_ids}
    return result


def extract_terms(question: str, graph: Graph, limit: int = 6) -> list[str]:
    """Pick the symbols in a question that actually exist in the graph.

    Deterministic: exact graph names first (longest first), then case-insensitive
    matches. A question that names nothing real retrieves nothing, rather than
    retrieving the whole repository.
    """
    names = {n.name for n in graph.nodes.values() if n.name}
    lowered = {}
    for name in names:
        lowered.setdefault(name.lower(), []).append(name)

    # Terms are gathered in two tiers. A word that is an exact graph symbol but
    # is ALSO ordinary English (`connect`, `file`, `format`, `get`) is held back:
    # in "how does X connect to Y" the user means the English word, and binding
    # it pulls an unrelated function into the answer. Such a word is only used
    # when nothing more specific matched.
    strong: list[str] = []
    weak: list[str] = []
    for raw in _TOKEN_RE.findall(question):
        if raw in names:
            bucket = weak if raw.lower() in _STOPWORDS else strong
            if raw not in bucket:
                bucket.append(raw)
            continue
        if raw.lower() in _STOPWORDS:
            continue
        for candidate in sorted(lowered.get(raw.lower(), [])):
            if candidate not in strong:
                strong.append(candidate)

    found = strong if strong else weak
    found.sort(key=lambda s: (-len(s), s))
    return found[:limit]


def retrieve(
    question: str,
    engine: QueryEngine,
    depth: int = 2,
    caller: str = "ask",
    log: Logger | None = None,
) -> Grounding:
    """Deterministic retrieval. This is the part that must be right."""
    log = log or NULL_LOGGER
    graph = engine.graph
    terms = extract_terms(question, graph)
    grounding = Grounding(question=question, terms=terms)

    if not terms:
        log.info("ask.no_terms", question_len=len(question))
        return grounding

    for term in terms[:3]:
        try:
            res = engine.impact(
                term, direction="both", depth=depth, include_snippets=True, caller=caller
            )
            grounding.results.append(prune(res))
        except Exception as exc:  # noqa: BLE001
            log.warn("ask.retrieve_failed", term=term, error=str(exc))

    # If the question mentions two symbols, the connection between them is
    # usually the actual answer.
    if len(terms) >= 2:
        try:
            path = engine.path_between(terms[0], terms[1], max_depth=depth + 2, caller=caller)
            if path.edges:
                grounding.results.append(path)
        except Exception as exc:  # noqa: BLE001
            log.warn("ask.path_failed", error=str(exc))

    seen: set[str] = set()
    for res in grounding.results:
        for node in res.nodes:
            if node["path"]:
                cite = f"{node['path']}:{node['start_line']}"
                if cite not in seen:
                    seen.add(cite)
                    grounding.citations.append(cite)
    grounding.citations.sort()
    log.info("ask.retrieved", terms=terms, results=len(grounding.results),
             citations=len(grounding.citations))
    return grounding


def merge_results(grounding: Grounding, max_nodes: int, max_edges: int) -> QueryResult:
    """Fold retrieved results into one capped QueryResult for the egress layer."""
    merged = QueryResult(operation="ask")
    seen_nodes: set[str] = set()
    seen_edges: set[tuple] = set()
    for res in grounding.results:
        for node in res.nodes:
            if node["id"] in seen_nodes:
                continue
            seen_nodes.add(node["id"])
            merged.nodes.append(node)
        for edge in res.edges:
            key = (edge["src"], edge["type"], edge["dst"], edge["line"])
            if key in seen_edges:
                continue
            seen_edges.add(key)
            merged.edges.append(edge)
        for nid, lines in res.snippets.items():
            merged.snippets.setdefault(nid, lines)
    # Carry the redaction count forward: it is security telemetry, and a merge
    # that silently reset it would report "no secrets found" on a payload that
    # had them removed.
    merged.redactions = sum(r.redactions for r in grounding.results)
    merged.nodes.sort(key=lambda n: n["id"])
    merged.edges.sort(key=lambda e: (e["src"], e["type"], e["dst"], e["line"]))
    merged.total_matched = len(merged.nodes)
    if len(merged.nodes) > max_nodes:
        merged.nodes = merged.nodes[:max_nodes]
        merged.truncated = True
    if len(merged.edges) > max_edges:
        merged.edges = merged.edges[:max_edges]
        merged.truncated = True
    keep = {n["id"] for n in merged.nodes}
    merged.snippets = {k: v for k, v in sorted(merged.snippets.items()) if k in keep}
    return merged


def structural_answer(grounding: Grounding) -> str:
    """The answer Orgono can give with no model at all.

    This is what `orgono ask` prints when egress is off, which is the default.
    It is not prose, but every line of it is a fact from the graph.
    """
    if grounding.is_empty():
        if not grounding.terms:
            return (
                "No symbol in that question matches anything in the graph.\n"
                "Try naming a function, class or file - `orgono find <name>` to look one up."
            )
        return f"Found no edges for: {', '.join(grounding.terms)}"

    lines: list[str] = []
    lines.append(f"symbols recognised: {', '.join(grounding.terms)}")
    for res in grounding.results:
        if not res.nodes and not res.edges:
            continue
        lines.append("")
        if res.operation == "path_between":
            lines.append("connection found:")
            for e in res.edges:
                lines.append(
                    f"  {e['src'].split('::')[-1]} -[{e['type']}]-> "
                    f"{e['dst'].split('::')[-1]}   {e['path']}:{e['line']}"
                )
            continue
        lines.append(f"{res.operation}: {res.total_matched} related node(s)")
        for n in res.nodes[:12]:
            loc = f"{n['path']}:{n['start_line']}" if n["path"] else "-"
            lines.append(f"  {n['kind']:<10} {n['name']:<28} {loc}")
    if grounding.citations:
        lines.append("")
        lines.append(f"citations: {', '.join(grounding.citations[:12])}")
    return "\n".join(lines)


def build_messages(grounding: Grounding, merged: QueryResult) -> list[dict]:
    """The prompt sent to whichever model the user chose."""
    context: list[str] = []
    for n in merged.nodes:
        context.append(f"{n['kind']} {n['name']} ({n['path']}:{n['start_line']})")
    for e in merged.edges[:200]:
        context.append(
            f"{e['src']} -[{e['type']}]-> {e['dst']}  @{e['path']}:{e['line']}"
        )
    for nid, lines in sorted(merged.snippets.items()):
        context.append(f"--- {nid}")
        context.extend(lines)

    system = (
        "You answer questions about a codebase using ONLY the supplied knowledge-graph "
        "context, which was extracted deterministically from the repository's AST. "
        "Every factual claim must cite a path:line drawn from the context. "
        "If the context does not answer the question, say exactly what is missing. "
        "Never invent a file, symbol or line number."
    )
    user = (
        f"Question: {grounding.question}\n\n"
        f"Symbols recognised in the graph: {', '.join(grounding.terms) or '(none)'}\n\n"
        f"Graph context:\n" + "\n".join(context)
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def parse_models_response(payload: dict) -> list[dict]:
    """Normalise an OpenRouter /models response.

    NOTE: openrouter.ai is unreachable from the build sandbox, so this parser is
    written against OpenRouter's documented response shape ({"data": [{"id", ...}]})
    and is exercised by tests against a recorded fixture, not a live call.
    """
    rows: list[dict] = []
    for item in payload.get("data", []) or []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        pricing = item.get("pricing") or {}
        def _num(v):
            try:
                return float(v)
            except (TypeError, ValueError):
                return None
        prompt_price = _num(pricing.get("prompt"))
        rows.append(
            {
                "id": item["id"],
                "name": item.get("name") or item["id"],
                "context_length": item.get("context_length"),
                "prompt_price": prompt_price,
                "completion_price": _num(pricing.get("completion")),
                "free": prompt_price == 0.0,
            }
        )
    rows.sort(key=lambda r: r["id"])
    return rows


def format_models(rows: list[dict], only_free: bool = False, limit: int = 60) -> str:
    if only_free:
        rows = [r for r in rows if r["free"]]
    if not rows:
        return "no models returned"
    out = [f"{len(rows)} model(s)"]
    for r in rows[:limit]:
        price = "free" if r["free"] else (
            f"${r['prompt_price']:.6g}/tok" if r["prompt_price"] is not None else "?"
        )
        ctx = f"{r['context_length']:,}" if r["context_length"] else "?"
        out.append(f"  {r['id']:<48} ctx={ctx:<10} {price}")
    if len(rows) > limit:
        out.append(f"  … {len(rows) - limit} more")
    return "\n".join(out)


def models_url(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/models"


def dumps(obj) -> str:
    return json.dumps(obj, indent=2, sort_keys=True, default=str)
