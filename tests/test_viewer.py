"""The viewer payload and its assets.

The browser behaviour itself is verified by rendering the page headlessly
(see the report); these tests pin the contract the page depends on.
"""

import json
import re

from orgono.app.core.viz import WEB_ROOT, build_payload, serve


def test_web_assets_are_present_and_vendored():
    assert (WEB_ROOT / "index.html").is_file()
    assert (WEB_ROOT / "app.js").is_file()
    assert (WEB_ROOT / "style.css").is_file()
    three = WEB_ROOT / "vendor" / "three.min.js"
    assert three.is_file(), "three.js must be vendored so the viewer works offline"
    assert three.stat().st_size > 100_000


def test_page_references_no_external_origin():
    """The viewer must not fetch anything from the network."""
    html = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
    js = (WEB_ROOT / "app.js").read_text(encoding="utf-8")
    assert "vendor/three.min.js" in html
    # The SVG namespace URI is an identifier, never fetched, so it is excluded.
    scrubbed = html.replace("http://www.w3.org/2000/svg", "")
    for marker in ("http://", "https://", "unpkg", "jsdelivr", "cdnjs"):
        assert marker not in scrubbed, f"viewer references an external origin: {marker}"
    # app.js may only fetch its own graph payload.
    for url in re.findall(r"""fetch\(\s*["']([^"']+)""", js):
        assert not url.startswith(("http://", "https://", "//")), f"app.js fetches {url}"


def test_payload_has_no_dangling_edges(golden_graph):
    payload = build_payload(golden_graph)
    ids = {n["id"] for n in payload["nodes"]}
    for edge in payload["edges"]:
        assert edge["src"] in ids and edge["dst"] in ids


def test_payload_is_bounded_for_huge_graphs(golden_graph):
    payload = build_payload(golden_graph, max_nodes=5, max_edges=5)
    assert len(payload["nodes"]) <= 5
    assert len(payload["edges"]) <= 5
    assert payload["truncated"] is True


def test_payload_is_json_serializable(golden_graph):
    assert json.loads(json.dumps(build_payload(golden_graph)))


def test_server_binds_loopback_by_default(golden_graph, silent_log):
    httpd, url = serve(golden_graph, host="127.0.0.1", port=0, log=silent_log, block=False)
    try:
        assert url.startswith("http://127.0.0.1:")
        assert httpd.server_address[0] == "127.0.0.1"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_graph_json_is_served(golden_graph, silent_log):
    import urllib.request
    httpd, url = serve(golden_graph, host="127.0.0.1", port=0, log=silent_log, block=False)
    try:
        base = url.rsplit("/", 1)[0]
        with urllib.request.urlopen(f"{base}/graph.json", timeout=5) as resp:
            data = json.loads(resp.read())
        assert data["nodes"]
    finally:
        httpd.shutdown()
        httpd.server_close()
