"""Local 3D viewer server.

Binds to 127.0.0.1 by default: the graph describes private source code, so it is
not exposed on the network unless the operator asks for that explicitly.
"""

from __future__ import annotations

import contextlib
import http.server
import json
import socketserver
import threading
from functools import partial
from pathlib import Path

from .graph import Graph
from .obs import NULL_LOGGER, Logger

WEB_ROOT = Path(__file__).resolve().parents[2] / "web"


class _Handler(http.server.SimpleHTTPRequestHandler):
    graph_payload: bytes = b"{}"
    logger: Logger = NULL_LOGGER

    def do_GET(self):  # noqa: N802
        if self.path.split("?")[0] in ("/graph.json", "/graph"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(self.graph_payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(self.graph_payload)
            return
        return super().do_GET()

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
        "root": graph.root,
        "nodes": [n.to_dict() for n in nodes],
        "edges": [e.to_dict() for e in edges],
        "stats": graph.stats,
        "truncated": truncated,
    }


def serve(
    graph: Graph,
    host: str = "127.0.0.1",
    port: int = 7373,
    log: Logger | None = None,
    block: bool = True,
) -> tuple[socketserver.TCPServer, str]:
    log = log or NULL_LOGGER
    payload = json.dumps(build_payload(graph), sort_keys=True).encode("utf-8")

    handler_cls = type(
        "OrgonoHandler",
        (_Handler,),
        {"graph_payload": payload, "logger": log},
    )
    handler = partial(handler_cls, directory=str(WEB_ROOT))

    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.TCPServer((host, port), handler)
    url = f"http://{host}:{httpd.server_address[1]}/index.html"
    log.info("viz.serving", url=url, nodes=len(graph.nodes), edges=len(graph.edges))
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
