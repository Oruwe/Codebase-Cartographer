"""orgono - terminal interface to the codebase cartographer.

Every command is `orgono <verb>`. Local commands never touch the network;
`orgono explain` is the only command that can, and it refuses unless explicitly
enabled after you have inspected the dry-run payload.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import getpass
import json
import os
import sys
import webbrowser

from . import __version__
from .app.core import ai, credentials
from .app.core.agent import Cartographer, tool_manifest
from .app.core.config import Config, StartupError, assert_bootable
from .app.core.egress import EgressRefused
from .app.core.graph import summarize
from .app.core.languages import LANGUAGES
from .app.core.obs import Logger, new_trace_id
from .app.core.query import QueryRefused

C_RESET = "\033[0m"
C_DIM = "\033[2m"
C_BOLD = "\033[1m"
C_CYAN = "\033[36m"
C_YELLOW = "\033[33m"
C_RED = "\033[31m"
C_GREEN = "\033[32m"


def _color(enabled: bool):
    if enabled:
        return C_RESET, C_DIM, C_BOLD, C_CYAN, C_YELLOW, C_RED, C_GREEN
    return ("",) * 7


def _use_color(args) -> bool:
    if getattr(args, "no_color", False) or os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


def _make_logger(args) -> Logger:
    # Human commands keep stdout clean; JSON logs go to stderr unless asked for.
    stream = sys.stdout if getattr(args, "log_stdout", False) else sys.stderr
    log = Logger(trace_id=new_trace_id(), stream=stream)
    if getattr(args, "quiet", False):
        log.enabled = False
    return log


def _config_from_args(args) -> Config:
    cfg = Config.from_env()
    overrides = {}
    for name in ("max_file_bytes", "max_files", "max_depth", "max_wall_seconds"):
        value = getattr(args, name, None)
        if value is not None:
            overrides[name] = value
    if overrides:
        cfg = cfg.with_limits(**overrides)
    if getattr(args, "enable_egress", False):
        cfg = dataclasses.replace(cfg, egress=dataclasses.replace(cfg.egress, enabled=True))
    if getattr(args, "no_dry_run", False):
        cfg = dataclasses.replace(cfg, egress=dataclasses.replace(cfg.egress, dry_run=False))
    if getattr(args, "model", None):
        cfg = dataclasses.replace(cfg, egress=dataclasses.replace(cfg.egress, model=args.model))
    return cfg


def _emit(payload, args) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def cmd_map(args) -> int:
    log = _make_logger(args)
    cart = Cartographer.create(args.path, _config_from_args(args), log)
    graph = cart.build(use_cache=not args.no_cache, write=not args.dry_run)
    if args.json:
        _emit(graph.stats, args)
        return 0
    r, d, b, c, y, red, g = _color(_use_color(args))
    s = graph.stats
    print(f"{b}{c}orgono{r} mapped {b}{cart.root}{r}")
    print(f"  {d}nodes{r}   {s['nodes']}   {d}edges{r} {s['edges']}   "
          f"{d}digest{r} {graph.digest()[:12]}")
    print(f"  {d}kinds{r}   " + "  ".join(f"{k}={v}" for k, v in s["node_kinds"].items()))
    print(f"  {d}edges{r}   " + "  ".join(f"{k}={v}" for k, v in s["edge_types"].items()))
    print(f"  {d}files{r}   " + "  ".join(f"{k}={v}" for k, v in s["file_status"].items()))
    if s["file_status"].get("unparsed"):
        print(f"  {y}note{r}    {s['file_status']['unparsed']} file(s) unparsed - "
              f"see `orgono report --unparsed`")
    print(f"  {d}took{r}    {s['elapsed_ms']:.0f} ms")
    if not args.dry_run:
        print(f"  {d}wrote{r}   {cart.graph_path()}")
    return 0


def cmd_view(args) -> int:
    from .app.core.viz import make_answerer, serve

    log = _make_logger(args)
    cfg = _config_from_args(args)
    cart = Cartographer.create(args.path, cfg, log)
    graph = cart.ensure_graph()
    r, d, b, c, y, red, g = _color(_use_color(args))
    answerer = None if args.no_assistant else make_answerer(graph, cart.root, cfg, log)
    httpd, url = serve(graph, host=args.host, port=args.port, log=log, block=False,
                       answerer=answerer)
    print(f"{b}{c}orgono{r} 3D map -> {b}{url}{r}")
    print(f"  {d}{len(graph.nodes)} nodes, {len(graph.edges)} edges. Ctrl-C to stop.{r}")
    if answerer:
        print(f"  {d}assistant: on (local only - answers never leave this machine){r}")
    else:
        print(f"  {y}assistant: off{r}")
    if not args.no_browser:
        with contextlib.suppress(Exception):
            webbrowser.open(url)
    try:
        import time
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nstopped.")
    finally:
        httpd.shutdown()
        httpd.server_close()
    return 0


def _print_result(result: dict, args) -> None:
    r, d, b, c, y, red, g = _color(_use_color(args))
    nodes = result.get("nodes", [])
    edges = result.get("edges", [])
    if not nodes and not edges:
        for note in result.get("notes", []):
            print(f"{y}{note}{r}")
        if not result.get("notes"):
            print(f"{d}no results{r}")
        return
    if nodes:
        print(f"{b}nodes{r} {d}({result.get('total_matched', len(nodes))} matched){r}")
        for n in nodes[:60]:
            loc = f"{n['path']}:{n['start_line']}" if n["path"] else "-"
            print(f"  {c}{n['kind']:<10}{r} {b}{n['name']:<28}{r} {d}{loc}{r}")
    if edges:
        print(f"{b}edges{r}")
        for e in edges[:60]:
            print(f"  {e['src'].split('::')[-1]:<30} {c}-{e['type']}->{r} "
                  f"{e['dst'].split('::')[-1]:<30} {d}{e['path']}:{e['line']}{r}")
    for nid, lines in list(result.get("snippets", {}).items())[:6]:
        print(f"{d}--- {nid}{r}")
        for line in lines:
            print(f"  {d}|{r} {line}")
    for note in result.get("notes", []):
        print(f"{y}note: {note}{r}")
    if result.get("truncated"):
        print(f"{y}note: response truncated by cap{r}")


def _run_tool(args, tool: str, **params) -> int:
    log = _make_logger(args)
    cart = Cartographer.create(args.path, _config_from_args(args), log)
    try:
        result = cart.call_tool(tool, caller=args.caller, **params)
    except QueryRefused as exc:
        print(f"{C_RED if _use_color(args) else ''}refused{C_RESET if _use_color(args) else ''}: {exc}",
              file=sys.stderr)
        return 2
    if args.json:
        _emit(result, args)
    else:
        _print_result(result, args)
    return 0


def cmd_find(args) -> int:
    return _run_tool(args, "find_symbol", name=args.name, kind=args.kind,
                     language=args.language, path_prefix=args.path_prefix,
                     exact=args.exact, include_snippets=args.snippets)


def cmd_impact(args) -> int:
    return _run_tool(args, "impact", symbol=args.symbol, direction=args.direction,
                     depth=args.depth, include_snippets=args.snippets)


def cmd_path(args) -> int:
    return _run_tool(args, "path_between", source=args.source, target=args.target,
                     max_depth=args.max_depth)


def cmd_file(args) -> int:
    return _run_tool(args, "file_summary", path=args.file)


def cmd_stats(args) -> int:
    log = _make_logger(args)
    cart = Cartographer.create(args.path, _config_from_args(args), log)
    stats = summarize(cart.ensure_graph())
    if args.json:
        _emit(stats, args)
        return 0
    r, d, b, c, y, red, g = _color(_use_color(args))
    for section in ("node_kinds", "edge_types", "languages", "file_status"):
        print(f"{b}{section}{r}")
        for k, v in stats[section].items():
            print(f"  {c}{k:<14}{r} {v}")
    print(f"{d}total {stats['nodes']} nodes / {stats['edges']} edges{r}")
    return 0


def cmd_report(args) -> int:
    log = _make_logger(args)
    cart = Cartographer.create(args.path, _config_from_args(args), log)
    graph = cart.ensure_graph()
    rows = [f for f in graph.sorted_files() if f.status != "parsed" or f.reason]
    if args.unparsed:
        rows = [f for f in rows if f.status == "unparsed"]
    if args.json:
        _emit([f.to_dict() for f in rows], args)
        return 0
    r, d, b, c, y, red, g = _color(_use_color(args))
    if not rows:
        print(f"{g}every file parsed cleanly{r}")
        return 0
    print(f"{b}files not fully parsed{r} {d}({len(rows)}){r}")
    for f in rows[:200]:
        tone = red if f.status == "unparsed" else y
        print(f"  {tone}{f.status:<12}{r} {f.path:<52} {d}{f.reason}{r}")
    return 0


def cmd_stacks(args) -> int:
    """What the repository is built with. Reads manifests; executes nothing."""
    from .app.core.stacks import detect_stacks

    report = detect_stacks(args.path, max_depth=args.depth)
    if args.json:
        _emit(report.to_dict(), args)
        return 0
    r, d, b, c, y, red, g = _color(_use_color(args))
    groups = report.by_category()
    if not groups:
        print(f"{d}no recognised stack manifests found{r}")
        return 0
    for category, items in groups.items():
        print(f"{b}{category}{r}")
        for item in items:
            detail = f" {d}({item.detail}){r}" if item.detail else ""
            print(f"  {c}{item.name:<28}{r}{detail} {d}<- {item.evidence}{r}")
    return 0


def cmd_tools(args) -> int:
    print(tool_manifest())
    return 0


def cmd_auth(args) -> int:
    r, d, b, c, y, red, g = _color(_use_color(args))
    root = getattr(args, "path", ".")
    if args.auth_cmd == "login":
        key = args.key
        if not key:
            if not sys.stdin.isatty():
                key = sys.stdin.read().strip()
            else:
                print(f"{d}Paste your OpenRouter API key. It is not echoed, and it is "
                      f"not saved unless you pass --save-env.{r}")
                key = getpass.getpass("API key: ").strip()
        if not key:
            print(f"{red}no key provided{r}", file=sys.stderr)
            return 1
        if args.save_env:
            try:
                target = credentials.save_to_env_file(key, root=root)
            except ValueError as exc:
                print(f"{red}{exc}{r}", file=sys.stderr)
                return 1
            print(f"{g}written{r} to {b}{target}{r} {d}(chmod 0600, added to .gitignore){r}")
        else:
            print(f"{g}key accepted for this session only.{r}")
            print(f"{d}Orgono does not store it. To keep it, either:{r}")
            print(f"  export {credentials.ENV_VARS[0]}=…      {d}# your shell{r}")
            print(f"  orgono auth login --save-env         {d}# writes to your .env{r}")
        print(f"{d}Having a key does not enable egress. Add --enable-egress when you "
              f"actually want a request to leave this machine.{r}")
        return 0

    if args.auth_cmd == "logout":
        print(f"{d}Orgono stores nothing, so there is nothing to remove.{r}")
        src = credentials.key_source(root=root)
        if src != "none":
            print(f"{y}A key is still visible via: {src}{r}")
            print(f"{d}Remove it there (unset the variable, or edit the .env).{r}")
        return 0

    key = credentials.resolve_api_key(root=root)
    print(f"{b}key{r}       {credentials.mask(key)}")
    print(f"{b}source{r}    {credentials.key_source(root=root)}")
    print(f"{b}looked in{r} " + ", ".join(str(p) for p in credentials.env_file_candidates(root)))
    cfg = Config.from_env()
    print(f"{b}egress{r}    enabled={cfg.egress.enabled} dry_run={cfg.egress.dry_run} "
          f"model={cfg.egress.model}")
    print(f"{d}Orgono never writes your key on its own.{r}")
    return 0


def cmd_models(args) -> int:
    """List models reachable through OpenRouter. Any id here works with --model."""
    r, d, b, c, y, red, g = _color(_use_color(args))
    cfg = _config_from_args(args)
    url = ai.models_url(cfg.egress.base_url)
    key = credentials.resolve_api_key(args.api_key, root=args.path)
    try:
        import requests
    except ImportError:
        print("this command needs the egress extra: pip install 'orgono[egress]'", file=sys.stderr)
        return 1
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    try:
        resp = requests.get(url, headers=headers, timeout=cfg.egress.timeout_seconds)
        resp.raise_for_status()
        rows = ai.parse_models_response(resp.json())
    except Exception as exc:  # noqa: BLE001
        print(f"{red}could not reach {url}{r}: {exc}", file=sys.stderr)
        print(f"{d}Any OpenRouter model id still works with --model.{r}", file=sys.stderr)
        return 1
    if args.json:
        _emit(rows, args)
        return 0
    print(ai.format_models(rows, only_free=args.free))
    return 0


def cmd_ask(args) -> int:
    """Grounded question answering. Retrieval is local; the model is optional."""
    log = _make_logger(args)
    cfg = _config_from_args(args)
    cart = Cartographer.create(args.path, cfg, log)
    engine = cart.engine()
    grounding = ai.retrieve(args.question, engine, depth=args.depth, caller=args.caller, log=log)
    r, d, b, c, y, red, g = _color(_use_color(args))

    if args.json and not args.send:
        _emit(grounding.to_dict(), args)
        return 0

    if not args.send:
        print(f"{b}{c}orgono{r} {d}(local answer - no model, nothing left this machine){r}")
        print(ai.structural_answer(grounding))
        if not grounding.is_empty():
            print(f"\n{d}For a written answer add:{r} --send --enable-egress --no-dry-run "
                  f"{d}[--model <id>]{r}")
        return 0

    merged = ai.merge_results(grounding, cfg.caps.max_nodes, cfg.caps.max_edges)
    key = credentials.resolve_api_key(args.api_key, root=args.path)
    try:
        outcome = cart.explain(args.question, merged, api_key=key, send=True)
    except EgressRefused as exc:
        print(f"{red}refused{r}: {exc}", file=sys.stderr)
        print(f"{d}The local answer above still stands:{r}", file=sys.stderr)
        print(ai.structural_answer(grounding))
        return 2
    if not outcome["sent"]:
        print(f"{y}DRY RUN - nothing left this machine.{r}")
        print(json.dumps(outcome["plan"], indent=2, sort_keys=True))
        return 0
    response = outcome["response"]
    try:
        text = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        _emit(response, args)
        return 0
    print(text)
    usage = response.get("usage") or {}
    if usage:
        print(f"\n{d}tokens: {usage}{r}")
    return 0


def cmd_explain(args) -> int:
    log = _make_logger(args)
    cfg = _config_from_args(args)
    cart = Cartographer.create(args.path, cfg, log)
    engine = cart.engine()
    try:
        if args.symbol:
            result = engine.impact(args.symbol, direction="both", depth=args.depth,
                                   include_snippets=True, caller=args.caller)
        else:
            result = engine.find_symbol(name=args.about, include_snippets=True,
                                        caller=args.caller)
    except QueryRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2

    api_key = credentials.resolve_api_key(args.api_key, root=args.path)
    try:
        outcome = cart.explain(args.question, result, api_key=api_key, send=args.send)
    except EgressRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2

    r, d, b, c, y, red, g = _color(_use_color(args))
    if not outcome["sent"]:
        print(f"{y}DRY RUN - nothing left this machine.{r}")
        print(f"{d}This is exactly what would be sent:{r}")
        print(json.dumps(outcome["plan"], indent=2, sort_keys=True))
        print(f"{d}To send: add --enable-egress --no-dry-run --send{r}")
        return 0
    _emit(outcome, args)
    return 0


def cmd_doctor(args) -> int:
    r, d, b, c, y, red, g = _color(_use_color(args))
    cfg = Config.from_env()
    ok = True
    print(f"{b}orgono{r} {__version__}")
    print(f"{b}python{r}    {sys.version.split()[0]}")
    from .app.core.languages import get_query, missing_languages

    missing = missing_languages()
    print(f"{b}languages{r} {d}({len(LANGUAGES) - len(missing)} of {len(LANGUAGES)} ready){r}")
    for name, spec in sorted(LANGUAGES.items()):
        if name in missing:
            print(f"  {y}--{r}   {name:<12} {d}not installed - pip install {missing[name]}{r}")
            continue
        try:
            get_query(name)
            print(f"  {g}ok{r}   {name:<12} {d}{', '.join(spec.extensions)}{r}")
        except Exception as exc:  # noqa: BLE001
            ok = False
            print(f"  {red}fail{r} {name:<12} {exc}")
    print(f"{b}limits{r}")
    for k, v in sorted(cfg.limits.__dict__.items()):
        print(f"  {c}{k:<20}{r} {v}")
    print(f"{b}query caps{r}")
    for k, v in sorted(cfg.caps.__dict__.items()):
        print(f"  {c}{k:<20}{r} {v}")
    print(f"{b}egress{r}")
    print(f"  {c}{'enabled':<20}{r} {cfg.egress.enabled} {d}(default off){r}")
    print(f"  {c}{'dry_run':<20}{r} {cfg.egress.dry_run}")
    print(f"  {c}{'api_key':<20}{r} {credentials.mask(credentials.resolve_api_key())} "
          f"{d}via {credentials.key_source()}{r}")
    print(f"  {c}{'key storage':<20}{r} {d}never written by orgono; read from env or your .env{r}")
    try:
        assert_bootable()
        print(f"  {c}{'boot check':<20}{r} {g}pass{r}")
    except StartupError as exc:
        ok = False
        print(f"  {c}{'boot check':<20}{r} {red}{exc}{r}")
    return 0 if ok else 1


def cmd_validate(args) -> int:
    """Run the OpenGAP validator if it is installed."""
    import shutil
    import subprocess

    exe = shutil.which("opengap") or shutil.which("gitagent")
    if not exe:
        print("opengap validator not found. Install with:\n"
              "  npm i -g @open-gitagent/opengap", file=sys.stderr)
        return 127
    proc = subprocess.run([exe, "validate"], cwd=args.path, check=False)
    return proc.returncode


# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="orgono",
        description="Orgono - a deterministic codebase cartographer. "
                    "Maps a repository's AST into a queryable knowledge graph.",
        epilog="Local commands never touch the network. `orgono explain` is the only "
               "command that can, and it is off by default.",
    )
    p.add_argument("--version", action="version", version=f"orgono {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp, with_limits: bool = False):
        sp.add_argument("-p", "--path", default=".", help="repository root (default: .)")
        sp.add_argument("--json", action="store_true", help="machine-readable output")
        sp.add_argument("--quiet", action="store_true", help="suppress JSON log lines")
        sp.add_argument("--no-color", action="store_true")
        sp.add_argument("--log-stdout", action="store_true",
                        help="send JSON logs to stdout instead of stderr")
        sp.add_argument("--caller", default="cli", help="caller identity recorded in the audit log")
        if with_limits:
            sp.add_argument("--max-file-bytes", type=int)
            sp.add_argument("--max-files", type=int)
            sp.add_argument("--max-depth", type=int)
            sp.add_argument("--max-wall-seconds", type=float)
        return sp

    m = common(sub.add_parser("map", help="build the knowledge graph"), with_limits=True)
    m.add_argument("--no-cache", action="store_true", help="ignore the content-hash cache")
    m.add_argument("--dry-run", action="store_true", help="build but do not write")
    m.set_defaults(func=cmd_map)

    v = common(sub.add_parser("view", help="open the interactive 3D map"), with_limits=True)
    v.add_argument("--port", type=int, default=7373)
    v.add_argument("--host", default="127.0.0.1", help="bind address (default: loopback only)")
    v.add_argument("--no-browser", action="store_true")
    v.add_argument("--no-assistant", action="store_true",
                   help="serve the map read-only, with no question endpoint")
    v.set_defaults(func=cmd_view)

    f = common(sub.add_parser("find", help="find symbols by name/kind/language"))
    f.add_argument("name", nargs="?", default=None)
    f.add_argument("--kind")
    f.add_argument("--language")
    f.add_argument("--path-prefix")
    f.add_argument("--exact", action="store_true")
    f.add_argument("--snippets", action="store_true")
    f.set_defaults(func=cmd_find)

    i = common(sub.add_parser("impact", help="what reaches, or is reached by, a symbol"))
    i.add_argument("symbol")
    i.add_argument("--direction", choices=("callers", "callees", "both"), default="callers")
    i.add_argument("--depth", type=int, default=2)
    i.add_argument("--snippets", action="store_true")
    i.set_defaults(func=cmd_impact)

    pa = common(sub.add_parser("path", help="how two symbols are connected"))
    pa.add_argument("source")
    pa.add_argument("target")
    pa.add_argument("--max-depth", type=int, default=5)
    pa.set_defaults(func=cmd_path)

    fl = common(sub.add_parser("file", help="summarize one file"))
    fl.add_argument("file")
    fl.set_defaults(func=cmd_file)

    common(sub.add_parser("stats", help="graph counts")).set_defaults(func=cmd_stats)

    rp = common(sub.add_parser("report", help="files that were skipped or failed to parse"))
    rp.add_argument("--unparsed", action="store_true", help="only genuine parse failures")
    rp.set_defaults(func=cmd_report)

    st = common(sub.add_parser("stacks", help="frameworks, build systems and infra in use"))
    st.add_argument("--depth", type=int, default=3, help="how deep to look for manifests")
    st.set_defaults(func=cmd_stacks)

    t = sub.add_parser("tools", help="print the tool surface offered to sibling agents")
    t.set_defaults(func=cmd_tools)

    a = sub.add_parser("auth", help="manage the API key used by `orgono explain`")
    a.add_argument("auth_cmd", choices=("login", "status", "logout"), nargs="?", default="status")
    a.add_argument("--key", help="pass the key directly (otherwise prompted, hidden)")
    a.add_argument("--save-env", action="store_true",
                   help="write the key into your .env (the only way orgono persists it)")
    a.add_argument("-p", "--path", default=".")
    a.add_argument("--no-color", action="store_true")
    a.set_defaults(func=cmd_auth)

    mo = common(sub.add_parser("models", help="list models available through OpenRouter"))
    mo.add_argument("--free", action="store_true", help="only zero-cost models")
    mo.add_argument("--api-key")
    mo.add_argument("--model")
    mo.set_defaults(func=cmd_models)

    ak = common(sub.add_parser("ask", help="ask a question about the codebase"))
    ak.add_argument("question")
    ak.add_argument("--depth", type=int, default=2)
    ak.add_argument("--api-key")
    ak.add_argument("--model", help="any OpenRouter model id")
    ak.add_argument("--send", action="store_true", help="use a model instead of the local answer")
    ak.add_argument("--enable-egress", action="store_true")
    ak.add_argument("--no-dry-run", action="store_true")
    ak.set_defaults(func=cmd_ask)

    e = common(sub.add_parser("explain", help="ask a model about the graph (OFF by default)"))
    e.add_argument("question")
    e.add_argument("--symbol", help="anchor the context on this symbol")
    e.add_argument("--about", help="anchor the context on symbols matching this name")
    e.add_argument("--depth", type=int, default=2)
    e.add_argument("--api-key")
    e.add_argument("--model")
    e.add_argument("--enable-egress", action="store_true",
                   help="allow the request to leave this machine")
    e.add_argument("--no-dry-run", action="store_true", help="stop printing and actually send")
    e.add_argument("--send", action="store_true", help="perform the request")
    e.set_defaults(func=cmd_explain)

    d = sub.add_parser("doctor", help="check grammars, limits and egress posture")
    d.add_argument("--no-color", action="store_true")
    d.set_defaults(func=cmd_doctor)

    val = sub.add_parser("validate", help="run the OpenGAP validator on this agent")
    val.add_argument("-p", "--path", default=".")
    val.set_defaults(func=cmd_validate)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        assert_bootable()
    except StartupError as exc:
        print(f"{exc}", file=sys.stderr)
        return 78  # EX_CONFIG
    try:
        return int(args.func(args) or 0)
    except (QueryRefused, EgressRefused) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
