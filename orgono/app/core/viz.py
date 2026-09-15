"""Local 3D viewer server, with a grounded question endpoint.

Binds to 127.0.0.1 by default: the graph describes private source code, so it is
not exposed on the network unless the operator asks for that explicitly.

The /api/ask endpoint is the reason this file takes security seriously. A server
on localhost that answers questions about your codebase is reachable by *any*
page your browser loads, so a drive-by request from an unrelated website could
otherwise read your code map. Three things prevent that:

  * a per-run bearer token, generated at start and injected into the page we
    serve, so a caller who never loaded our page cannot call the API;
  * an Origin/Host check, rejecting cross-origin callers outright;
  * the same QueryEngine caps the CLI uses, so even an authorised caller cannot
    sweep the graph.
"""

from __future__ import annotations

import contextlib
import http.server
import json
import secrets
import socketserver
import threading
import time
from functools import partial
from pathlib import Path

from .config import Config
from .graph import Graph
from .obs import NULL_LOGGER, Logger

WEB_ROOT = Path(__file__).resolve().parents[2] / "web"

MAX_REQUEST_BYTES = 8_192
MAX_QUESTION_CHARS = 500
RATE_LIMIT_PER_MINUTE = 60


class _Handler(http.server.SimpleHTTPRequestHandler):
    graph_payload: bytes = b"{}"
    logger: Logger = NULL_LOGGER
    token: str = ""
    answerer = None            # callable(question, depth) -> dict
    allowed_hosts: tuple = ()
    _hits: list = []

    # -- helpers --------------------------------------------------------
    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _origin_ok(self) -> bool:
        """Refuse any caller that is not our own page.

        A page on example.com can POST to 127.0.0.1 without reading the reply,
        so the Origin header is checked before anything is done, not after.
        """
        origin = self.headers.get("Origin")
        if origin is not None:
            host = origin.split("//", 1)[-1]
            return host in self.allowed_hosts
        # No Origin (a same-origin fetch or a CLI client): fall back to Host.
        host = (self.headers.get("Host") or "").strip()
        return host in self.allowed_hosts

    def _rate_limited(self) -> bool:
        now = time.time()
        cls = type(self)
        cls._hits = [t for t in cls._hits if now - t < 60]
        if len(cls._hits) >= RATE_LIMIT_PER_MINUTE:
            return True
        cls._hits.append(now)
        return False

    def _authorised(self) -> bool:
        header = self.headers.get("Authorization", "")
        supplied = header[7:] if header.startswith("Bearer ") else ""
        return bool(self.token) and secrets.compare_digest(supplied, self.token)

    # -- routes ---------------------------------------------------------
    def do_GET(self):  # noqa: N802
        route = self.path.split("?")[0]
        if route in ("/graph.json", "/graph"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(self.graph_payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(self.graph_payload)
            return
        if route in ("/", "/index.html"):
            return self._serve_page()
        return super().do_GET()

    def _serve_page(self) -> None:
        """Serve index.html with this run's token injected.

        The token never travels in a URL (it would end up in history and logs);
        it is written into the document we serve and read back by our own script.
        """
        try:
            html = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
        except OSError:
            self.send_error(404)
            return
        html = html.replace("__ORGONO_TOKEN__", self.token)
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802
        if self.path.split("?")[0] != "/api/ask":
            return self._json(404, {"error": "no such endpoint"})
        if not self._origin_ok():
            self.logger.warn("viz.rejected", reason="cross_origin",
                             origin=self.headers.get("Origin"))
            return self._json(403, {"error": "cross-origin requests are refused"})
        if not self._authorised():
            self.logger.warn("viz.rejected", reason="bad_token")
            return self._json(401, {"error": "missing or invalid session token"})
        if self._rate_limited():
            return self._json(429, {"error": "too many requests"})

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._json(400, {"error": "bad Content-Length"})
        if length <= 0 or length > MAX_REQUEST_BYTES:
            return self._json(413, {"error": "request too large"})

        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return self._json(400, {"error": "body must be JSON"})
        if not isinstance(payload, dict):
            return self._json(400, {"error": "body must be a JSON object"})

        question = str(payload.get("question", "")).strip()[:MAX_QUESTION_CHARS]
        try:
            depth = int(payload.get("depth", 2))
        except (TypeError, ValueError):
            depth = 2
        depth = max(1, min(depth, 5))
        if not question:
            return self._json(400, {"error": "a question is required"})
        if self.answerer is None:
            return self._json(503, {"error": "this viewer was started without a query engine"})

        try:
            answer = self.answerer(question, depth)
        except PermissionError as exc:
            return self._json(403, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            self.logger.error("viz.ask_failed", error=str(exc))
            return self._json(500, {"error": "the query could not be answered"})
        return self._json(200, answer)

    def log_message(self, fmt, *args):  # keep stdout as single-line JSON only
        with contextlib.suppress(Exception):
            self.logger.debug("viz.request", path=self.path, client=self.client_address[0])


def build_payload(graph: Graph, max_nodes: int = 6000, max_edges: int = 20000) -> dict:
    """Viewer payload. Bounded so a huge graph cannot wedge the browser."""
    nodes = graph.sorted_nodes()
    edges = graph.sorted_edges()
    truncated = False
    if len(nodes) > max_nodes:
        degree: dict[str, int] = {}
        for e in edges:
            degree[e.src] = degree.get(e.src, 0) + 1
            degree[e.dst] = degree.get(e.dst, 0) + 1
        nodes = sorted(nodes, key=lambda n: (-degree.get(n.id, 0), n.id))[:max_nodes]
        truncated = True
    keep = {n.id for n in nodes}
    edges = [e for e in edges if e.src in keep and e.dst in keep]
    if len(edges) > max_edges:
        edges = edges[:max_edges]
        truncated = True
    return {
        "root": Path(graph.root).name if graph.root else "",
        "nodes": [n.to_dict() for n in nodes],
        "edges": [e.to_dict() for e in edges],
        "stats": graph.stats,
        "truncated": truncated,
    }


def make_answerer(graph: Graph, root: str | Path, config: Config, log: Logger):
    """Build the callable that answers a question from the graph.

    The answer is produced by the same deterministic retrieval the CLI uses, and
    is capped by the same QueryCaps. No model is involved: this endpoint never
    sends anything off the machine, whatever the egress settings say.
    """
    from . import ai
    from .query import QueryEngine

    def answer(question: str, depth: int) -> dict:
        engine = QueryEngine(graph, caps=config.caps, log=log, root=root)
        grounding = ai.retrieve(question, engine, depth=depth, caller="viewer", log=log)
        merged = ai.merge_results(grounding, config.caps.max_nodes, config.caps.max_edges)
        return {
            "question": question,
            "terms": grounding.terms,
            "answer": ai.structural_answer(grounding),
            "nodes": [n["id"] for n in merged.nodes],
            "edges": merged.edges[: config.caps.max_edges],
            "citations": grounding.citations[:20],
            "snippets": merged.snippets,
            "redactions": merged.redactions,
            "model_used": False,
        }

    return answer


def serve(
    graph: Graph,
    host: str = "127.0.0.1",
    port: int = 7373,
    log: Logger | None = None,
    block: bool = True,
    answerer=None,
) -> tuple[socketserver.TCPServer, str]:
    log = log or NULL_LOGGER
    payload = json.dumps(build_payload(graph), sort_keys=True).encode("utf-8")
    token = secrets.token_urlsafe(32)

    socketserver.TCPServer.allow_reuse_address = True
    probe = socketserver.TCPServer((host, port), None, bind_and_activate=False)
    probe.server_bind()
    bound_host, bound_port = probe.server_address[0], probe.server_address[1]
    probe.server_close()

    allowed = (
        f"{bound_host}:{bound_port}",
        f"localhost:{bound_port}",
        f"127.0.0.1:{bound_port}",
    )

    handler_cls = type(
        "OrgonoHandler",
        (_Handler,),
        {
            "graph_payload": payload,
            "logger": log,
            "token": token,
            "answerer": staticmethod(answerer) if answerer else None,
            "allowed_hosts": allowed,
            "_hits": [],
        },
    )
    handler = partial(handler_cls, directory=str(WEB_ROOT))

    httpd = socketserver.TCPServer((bound_host, bound_port), handler)
    url = f"http://{bound_host}:{httpd.server_address[1]}/index.html"
    log.info("viz.serving", url=url, nodes=len(graph.nodes), edges=len(graph.edges),
             ask_enabled=answerer is not None)
    if block:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            httpd.server_close()
    else:
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, url
