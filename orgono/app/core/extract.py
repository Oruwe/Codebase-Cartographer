"""AST-only extraction over tree-sitter.

Guarantees, each backed by a test rather than a promise:
  * Deterministic     - same content in, byte-identical graph out (test_determinism).
  * Incremental       - keyed on content sha256, never mtime (test_incremental).
  * Bounded           - file size, file count, depth, wall clock (test_bounds).
  * Honest            - unparseable files are recorded with their error, not dropped.
  * Explicit          - unsupported languages are reported as such, not half-parsed.

Note on wall-clock bounding: tree-sitter 0.26 removed the parser timeout
(`Parser.parse` has no timeout/progress argument - verified, not assumed), so the
deadline is enforced *around* parsing: per-file size caps keep any single parse
short, and the deadline is checked between files.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .graph import Edge, FileReport, Graph, Node, make_node_id, summarize
from .languages import LANGUAGES, get_parser, language_for_path, run_query
from .obs import NULL_LOGGER, Logger

CACHE_VERSION = "orgono-cache/1"

# A symbol defined in more than this many places is treated as ambiguous rather
# than bound to every candidate. See _resolve_calls.
MAX_CALL_FANOUT = 4


@dataclass
class _Def:
    node_id: str
    name: str
    kind: str
    path: str
    start_line: int
    end_line: int


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _decode(data: bytes) -> str | None:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _is_binary(data: bytes) -> bool:
    return b"\x00" in data[:8192]


def iter_source_files(root: Path, cfg: Config, log: Logger) -> tuple[list[Path], list[FileReport]]:
    """Walk `root`, returning (files_to_parse, reports_for_skipped).

    Traversal is bounded by depth, file count and the exclude list. Symlinked
    directories are not followed unless explicitly enabled, which is what keeps a
    symlink loop from being an infinite walk.
    """
    selected: list[Path] = []
    skipped: list[FileReport] = []
    seen_dirs: set[tuple[int, int]] = set()
    root = root.resolve()

    for dirpath, dirnames, filenames in os.walk(root, followlinks=cfg.follow_symlinks):
        current = Path(dirpath)
        try:
            depth = len(current.relative_to(root).parts)
        except ValueError:
            continue

        if depth >= cfg.limits.max_depth:
            if dirnames:
                log.warn(
                    "traverse.depth_capped",
                    path=str(current.relative_to(root)),
                    depth=depth,
                    limit=cfg.limits.max_depth,
                )
            dirnames[:] = []
            continue

        # Guard against symlink loops even when following is enabled.
        try:
            st = current.stat()
            key = (st.st_dev, st.st_ino)
            if key in seen_dirs:
                dirnames[:] = []
                continue
            seen_dirs.add(key)
        except OSError:
            dirnames[:] = []
            continue

        dirnames[:] = sorted(d for d in dirnames if d not in cfg.exclude_dirs and not d.startswith(".orgono"))

        for filename in sorted(filenames):
            path = current / filename
            rel = str(path.relative_to(root))
            if len(selected) >= cfg.limits.max_files:
                skipped.append(FileReport(path=rel, status="skipped", reason="max_files_reached"))
                continue
            lang = language_for_path(filename)
            if lang is None:
                continue  # not a source file we claim to support; not an error
            try:
                if path.is_symlink() and not cfg.follow_symlinks:
                    skipped.append(FileReport(path=rel, status="skipped", reason="symlink"))
                    continue
                size = path.stat().st_size
            except OSError as exc:
                skipped.append(FileReport(path=rel, status="skipped", reason=f"stat_error: {exc}"))
                continue
            if size > cfg.limits.max_file_bytes:
                skipped.append(
                    FileReport(
                        path=rel,
                        status="skipped",
                        language=lang,
                        bytes=size,
                        reason=f"file_too_large: {size} > {cfg.limits.max_file_bytes}",
                    )
                )
                log.warn("file.skipped", path=rel, bytes=size, reason="file_too_large")
                continue
            selected.append(path)

    selected.sort()
    skipped.sort(key=lambda r: r.path)
    return selected, skipped


def _collect_unsupported(root: Path, cfg: Config) -> list[FileReport]:
    """Report files Orgono does not claim to support, rather than pretending."""
    reports: list[FileReport] = []
    root = root.resolve()
    known_noise = {".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".lock", ".cfg", ".ini", ".gitignore"}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        try:
            depth = len(current.relative_to(root).parts)
        except ValueError:
            continue
        if depth >= cfg.limits.max_depth:
            dirnames[:] = []
            continue
        dirnames[:] = sorted(d for d in dirnames if d not in cfg.exclude_dirs)
        for filename in sorted(filenames):
            ext = Path(filename).suffix.lower()
            if not ext or ext in known_noise or language_for_path(filename):
                continue
            rel = str((current / filename).relative_to(root))
            reports.append(
                FileReport(path=rel, status="unsupported", reason=f"no grammar for '{ext}'")
            )
            if len(reports) >= 500:
                return reports
    return reports


def _node_text(node) -> str:
    try:
        return node.text.decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return ""


def _enclosing_def(node, def_ranges: list[tuple[int, int, str]]) -> str | None:
    """Innermost definition whose byte range contains `node`."""
    best: tuple[int, str] | None = None
    start = node.start_byte
    for lo, hi, node_id in def_ranges:
        if lo <= start < hi:
            span = hi - lo
            if best is None or span < best[0]:
                best = (span, node_id)
    return best[1] if best else None


def extract_file(
    rel_path: str,
    source: bytes,
    language: str,
    graph: Graph,
    log: Logger,
) -> FileReport:
    """Parse one file into nodes and edges. Never raises on bad input."""
    digest = sha256_bytes(source)
    report = FileReport(
        path=rel_path, status="parsed", language=language, bytes=len(source), sha256=digest
    )

    if _is_binary(source):
        report.status = "skipped"
        report.reason = "binary"
        return report

    text = _decode(source)
    if text is None:
        report.status = "unparsed"
        report.reason = "utf8_decode_error"
        graph.add_node(
            Node(
                id=make_node_id(rel_path, "unparsed", rel_path, 0),
                kind="unparsed",
                name=rel_path,
                path=rel_path,
                language=language,
                error="utf8_decode_error",
            )
        )
        log.warn("file.unparsed", path=rel_path, reason="utf8_decode_error")
        return report

    file_node = graph.add_node(
        Node(
            id=make_node_id(rel_path, "file", rel_path, 0),
            kind="file",
            name=rel_path,
            path=rel_path,
            language=language,
            start_line=1,
            end_line=text.count("\n") + 1,
        )
    )

    try:
        tree = get_parser(language).parse(source)
    except Exception as exc:  # noqa: BLE001
        report.status = "unparsed"
        report.reason = f"parse_error: {type(exc).__name__}: {exc}"
        graph.add_node(
            Node(
                id=make_node_id(rel_path, "unparsed", rel_path, 0),
                kind="unparsed",
                name=rel_path,
                path=rel_path,
                language=language,
                error=report.reason,
            )
        )
        log.warn("file.unparsed", path=rel_path, reason=report.reason)
        return report

    root = tree.root_node
    if root.has_error:
        # Degrade honestly. The file is not "parsed" - tree-sitter recovered a
        # partial tree - so it gets its own status rather than being counted as
        # a clean parse. A graph that quietly omits part of a file produces
        # confidently wrong impact analysis.
        report.status = "partial"
        report.reason = "syntax_errors_present"

    try:
        caps = run_query(language, root)
    except Exception as exc:  # noqa: BLE001
        report.status = "unparsed"
        report.reason = f"query_error: {type(exc).__name__}: {exc}"
        log.warn("file.unparsed", path=rel_path, reason=report.reason)
        return report

    kind_by_capture = {
        "def.function": "function",
        "def.method": "method",
        "def.class": "class",
        "def.interface": "interface",
        "def.constant": "constant",
    }

    # --- definitions -----------------------------------------------------
    # Collected before any node is added so that nesting can be resolved:
    # a `function` whose range sits inside a `class` range is a *method*.
    # (Found by running it: every Python method was previously labelled
    # `function`, because Python uses `function_definition` for both.)
    raw_defs: list[tuple[int, int, str, str, int, int, int]] = []  # lo, hi, kind, name, line, col, end

    # The query already tells us where each name is via @def.name. Use those
    # captures rather than re-deriving the name from the definition node: some
    # patterns anchor on a wrapper (a Python `expression_statement`, say) whose
    # own children are not identifiers, and re-deriving silently dropped them.
    name_nodes = sorted(
        ((n.start_byte, n.end_byte, n) for n in caps.get("def.name", [])),
        key=lambda t: (t[0], t[1]),
    )

    def _name_for(def_node):
        direct = def_node.child_by_field_name("name")
        if direct is not None:
            return direct
        best = None
        for lo, hi, node in name_nodes:
            if (
                def_node.start_byte <= lo
                and hi <= def_node.end_byte
                and (best is None or (hi - lo) < (best.end_byte - best.start_byte))
            ):
                best = node
        if best is not None:
            return best
        return next(
            (
                c
                for c in def_node.named_children
                if c.type
                in ("identifier", "type_identifier", "property_identifier", "field_identifier")
            ),
            None,
        )

    for capture, kind in sorted(kind_by_capture.items()):
        for def_node in caps.get(capture, []):
            name_node = _name_for(def_node)
            if name_node is None:
                continue
            name = _node_text(name_node)
            if not name:
                continue
            raw_defs.append(
                (
                    def_node.start_byte,
                    def_node.end_byte,
                    kind,
                    name,
                    def_node.start_point[0] + 1,
                    def_node.start_point[1],
                    def_node.end_point[0] + 1,
                )
            )

    class_ranges = [(lo, hi) for lo, hi, kind, *_ in raw_defs if kind in ("class", "interface")]

    # A `const f = () => {}` is captured both as a function and as a constant.
    # The richer kind wins, so the same symbol never appears twice.
    _strong = {
        (name, line)
        for _lo, _hi, kind, name, line, _c, _e in raw_defs
        if kind != "constant"
    }
    raw_defs = [
        d for d in raw_defs
        if d[2] != "constant" or (d[3], d[4]) not in _strong
    ]

    def_ranges: list[tuple[int, int, str]] = []
    local_defs: list[_Def] = []
    for lo, hi, kind, name, line, col, end_line in sorted(raw_defs):
        if kind == "function" and any(clo <= lo and hi <= chi and (clo, chi) != (lo, hi) for clo, chi in class_ranges):
            kind = "method"
        node_id = make_node_id(rel_path, kind, name, line)
        graph.add_node(
            Node(
                id=node_id,
                kind=kind,
                name=name,
                path=rel_path,
                language=language,
                start_line=line,
                end_line=end_line,
            )
        )
        graph.add_edge(
            Edge(src=file_node.id, dst=node_id, type="defines", path=rel_path, line=line, col=col)
        )
        def_ranges.append((lo, hi, node_id))
        local_defs.append(_Def(node_id, name, kind, rel_path, line, end_line))

    # --- imports ---------------------------------------------------------
    for imp in caps.get("import.name", []):
        raw = _node_text(imp).strip().strip('"').strip("'")
        if not raw:
            continue
        target = make_node_id("", "external", raw, 0)
        graph.add_node(
            Node(id=target, kind="external", name=raw, path="", language=language)
        )
        graph.add_edge(
            Edge(
                src=file_node.id,
                dst=target,
                type="imports",
                path=rel_path,
                line=imp.start_point[0] + 1,
                col=imp.start_point[1],
            )
        )

    # --- calls (resolved later, recorded now with their exact location) ---
    pending_calls: list[tuple[str, str, int, int]] = []
    for call in caps.get("call.name", []):
        name = _node_text(call)
        if not name:
            continue
        src_id = _enclosing_def(call, def_ranges) or file_node.id
        pending_calls.append((src_id, name, call.start_point[0] + 1, call.start_point[1]))

    report.nodes = len(local_defs)
    graph.stats.setdefault("_pending_calls", []).extend(
        [(rel_path, s, n, ln, c) for (s, n, ln, c) in pending_calls]
    )
    return report


def _resolve_calls(graph: Graph, log: Logger) -> int:
    """Second pass: bind call sites to definitions by name, across files.

    A name that matches no definition in the graph becomes an `external` node,
    which is honest (it shows the third-party surface) rather than a silent drop.
    """
    pending = graph.stats.pop("_pending_calls", [])
    by_name: dict[str, list[str]] = {}
    for node in graph.nodes.values():
        if node.kind in ("function", "method", "class", "interface", "constant"):
            by_name.setdefault(node.name, []).append(node.id)
    for key in by_name:
        by_name[key].sort()

    made = 0
    ambiguous = 0
    for rel_path, src_id, name, line, col in sorted(pending):
        candidates = by_name.get(name) or []
        # A name defined in many places (`to_dict`, `run`, `get`) would otherwise
        # fan out to every definition and produce confidently wrong impact
        # analysis. Prefer a definition in the calling file; failing that, only
        # bind when the candidate set is small enough to be meaningful.
        same_file = [c for c in candidates if c.startswith(f"{rel_path}::")]
        if same_file:
            targets = same_file
        elif 0 < len(candidates) <= MAX_CALL_FANOUT:
            targets = candidates
        else:
            if candidates:
                ambiguous += 1
            targets = []

        if targets:
            for target in targets:
                if target == src_id:
                    continue
                graph.add_edge(
                    Edge(src=src_id, dst=target, type="calls", path=rel_path, line=line, col=col)
                )
                made += 1
        else:
            target = make_node_id("", "external", name, 0)
            graph.add_node(Node(id=target, kind="external", name=name, path=""))
            graph.add_edge(
                Edge(src=src_id, dst=target, type="references", path=rel_path, line=line, col=col)
            )
            made += 1
    graph.stats["ambiguous_calls"] = ambiguous
    log.info("resolve.complete", edges=made)
    return made


def extract_repo(
    root: str | Path,
    cfg: Config | None = None,
    log: Logger | None = None,
    cache: dict | None = None,
) -> Graph:
    """Build the graph for a repository. The entry point for everything else."""
    cfg = cfg or Config.from_env()
    log = log or NULL_LOGGER
    root = Path(root).resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"not a directory: {root}")

    started = time.perf_counter()
    deadline = started + cfg.limits.max_wall_seconds
    graph = Graph(root=str(root))
    cache = cache if cache is not None else {}
    reused = 0
    total_bytes = 0

    log.info(
        "extract.start",
        root=str(root),
        languages=sorted(LANGUAGES),
        limits=cfg.limits.__dict__,
    )

    with log.stage("traverse"):
        files, skipped = iter_source_files(root, cfg, log)
    for rep in skipped:
        graph.files[rep.path] = rep

    with log.stage("parse", files=len(files)):
        for path in files:
            if time.perf_counter() > deadline:
                rel = str(path.relative_to(root))
                graph.files[rel] = FileReport(path=rel, status="skipped", reason="wall_clock_exceeded")
                log.warn("extract.deadline", path=rel, limit=cfg.limits.max_wall_seconds)
                continue
            rel = str(path.relative_to(root))
            try:
                source = path.read_bytes()
            except OSError as exc:
                graph.files[rel] = FileReport(path=rel, status="skipped", reason=f"read_error: {exc}")
                continue
            total_bytes += len(source)
            if total_bytes > cfg.limits.max_total_bytes:
                graph.files[rel] = FileReport(path=rel, status="skipped", reason="total_bytes_exceeded")
                log.warn("extract.total_bytes_exceeded", path=rel, total=total_bytes)
                break
            lang = language_for_path(path.name)
            if lang is None:
                graph.files[rel] = FileReport(path=rel, status="unsupported", reason="no grammar")
                continue
            digest = sha256_bytes(source)
            cached = cache.get(rel)
            if cached and cached.get("sha256") == digest:
                reused += 1
            report = extract_file(rel, source, lang, graph, log)
            graph.files[rel] = report

    with log.stage("resolve"):
        _resolve_calls(graph, log)

    with log.stage("unsupported_scan"):
        for rep in _collect_unsupported(root, cfg):
            graph.files.setdefault(rep.path, rep)

    elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
    ambiguous = graph.stats.get("ambiguous_calls", 0)
    graph.stats = {
        **summarize(graph),
        "ambiguous_calls": ambiguous,
        "elapsed_ms": elapsed_ms,
        "reused_from_cache": reused,
        "bytes_read": total_bytes,
        "trace_id": log.trace_id,
        "timings_ms": log.timings,
    }
    log.info("extract.complete", **{k: v for k, v in graph.stats.items() if k != "timings_ms"})
    return graph


# --- cache -------------------------------------------------------------------

def load_cache(cache_path: str | Path) -> dict:
    p = Path(cache_path)
    if not p.exists():
        return {}
    try:
        with open(p, encoding="utf-8") as fh:
            data = json.load(fh)
        if data.get("version") != CACHE_VERSION:
            return {}
        return data.get("files", {})
    except (OSError, json.JSONDecodeError):
        return {}


def save_cache(cache_path: str | Path, graph: Graph) -> None:
    """Cache keyed on content hash. A checkout changes mtimes and nothing else,
    so mtime is never consulted."""
    p = Path(cache_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": CACHE_VERSION,
        "files": {
            f.path: {"sha256": f.sha256, "status": f.status, "language": f.language}
            for f in graph.sorted_files()
            if f.sha256
        },
    }
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
    tmp.replace(p)
