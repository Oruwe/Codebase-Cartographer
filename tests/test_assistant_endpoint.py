"""The in-viewer assistant endpoint.

A localhost server that answers questions about your source code is reachable by
every page your browser loads. These tests are the reason that is safe: a
per-run bearer token, an Origin check, a body-size cap and a rate limit, all
verified by trying to get past them.
"""

import json
import urllib.error
import urllib.request

import pytest

from orgono.app.core.config import Config, QueryCaps
from orgono.app.core.viz import make_answerer, serve


@pytest.fixture
def viewer(golden_graph, golden_dir, silent_log):
    answerer = make_answerer(golden_graph, golden_dir, Config(), silent_log)
    httpd, url = serve(golden_graph, host="127.0.0.1", port=0, log=silent_log,
                       block=False, answerer=answerer)
    base = url.rsplit("/", 1)[0]
    page = urllib.request.urlopen(f"{base}/index.html", timeout=5).read().decode()
    token = page.split('__ORGONO_TOKEN = "', 1)[1].split('"', 1)[0]
    yield base, token
    httpd.shutdown()
    httpd.server_close()


def _post(base, body, token=None, origin=None, raw=None):
    data = raw if raw is not None else json.dumps(body).encode()
    req = urllib.request.Request(f"{base}/api/ask", data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Host", base.split("//", 1)[1])
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if origin:
        req.add_header("Origin", origin)
    return urllib.request.urlopen(req, timeout=10)


def test_a_real_question_is_answered_from_the_graph(viewer):
    base, token = viewer
    resp = _post(base, {"question": "what calls query_users?"}, token=token, origin=base)
    data = json.loads(resp.read())
    assert resp.status == 200
    assert "query_users" in data["terms"]
    assert data["nodes"], "an answer should highlight the nodes it cites"
    assert data["model_used"] is False, "the endpoint must never call a model"


def test_the_token_is_required(viewer):
    base, _token = viewer
    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(base, {"question": "what calls query_users?"}, origin=base)
    assert exc.value.code == 401


def test_a_wrong_token_is_rejected(viewer):
    base, _token = viewer
    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(base, {"question": "x"}, token="not-the-token", origin=base)
    assert exc.value.code == 401


def test_a_drive_by_request_from_another_site_is_refused(viewer):
    """Any page you visit can POST to localhost. Even with a valid token, a
    cross-origin caller must be refused."""
    base, token = viewer
    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(base, {"question": "what calls query_users?"}, token=token,
              origin="https://evil.example.com")
    assert exc.value.code == 403


def test_an_oversized_body_is_refused(viewer):
    base, token = viewer
    payload = json.dumps({"question": "A" * 20_000}).encode()
    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(base, None, token=token, origin=base, raw=payload)
    assert exc.value.code == 413


def test_a_non_json_body_is_refused(viewer):
    base, token = viewer
    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(base, None, token=token, origin=base, raw=b"not json at all")
    assert exc.value.code == 400


def test_an_empty_question_is_refused(viewer):
    base, token = viewer
    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(base, {"question": "   "}, token=token, origin=base)
    assert exc.value.code == 400


def test_unknown_endpoints_are_404(viewer):
    base, token = viewer
    req = urllib.request.Request(f"{base}/api/exfiltrate", data=b"{}", method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req, timeout=5)
    assert exc.value.code == 404


def test_responses_are_capped_by_the_same_query_caps(golden_graph, golden_dir, silent_log):
    cfg = Config(caps=QueryCaps(max_nodes=3, max_edges=3))
    answerer = make_answerer(golden_graph, golden_dir, cfg, silent_log)
    data = answerer("what calls query_users?", 2)
    assert len(data["nodes"]) <= 3
    assert len(data["edges"]) <= 3


def test_the_token_is_not_placed_in_a_url(viewer):
    """A token in a query string ends up in history, logs and Referer headers."""
    base, token = viewer
    page = urllib.request.urlopen(f"{base}/index.html", timeout=5).read().decode()
    assert f"?token={token}" not in page
    assert f"&token={token}" not in page


def test_each_run_issues_a_fresh_token(golden_graph, golden_dir, silent_log):
    tokens = set()
    for _ in range(2):
        answerer = make_answerer(golden_graph, golden_dir, Config(), silent_log)
        httpd, url = serve(golden_graph, host="127.0.0.1", port=0, log=silent_log,
                           block=False, answerer=answerer)
        base = url.rsplit("/", 1)[0]
        page = urllib.request.urlopen(f"{base}/index.html", timeout=5).read().decode()
        tokens.add(page.split('__ORGONO_TOKEN = "', 1)[1].split('"', 1)[0])
        httpd.shutdown()
        httpd.server_close()
    assert len(tokens) == 2, "tokens must not be reused between runs"


def test_the_endpoint_is_absent_when_the_assistant_is_disabled(golden_graph, silent_log):
    httpd, url = serve(golden_graph, host="127.0.0.1", port=0, log=silent_log,
                       block=False, answerer=None)
    base = url.rsplit("/", 1)[0]
    page = urllib.request.urlopen(f"{base}/index.html", timeout=5).read().decode()
    token = page.split('__ORGONO_TOKEN = "', 1)[1].split('"', 1)[0]
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post(base, {"question": "x"}, token=token, origin=base)
        assert exc.value.code == 503
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_answers_never_contain_unredacted_secrets(tmp_path, silent_log):
    from orgono.app.core.extract import extract_repo
    (tmp_path / "s.py").write_text(
        "def loader():\n"
        '    key = "sk-proj-VIEWERSECRET1234567890abcd"\n'
        "    return key\n"
    )
    graph = extract_repo(tmp_path, Config(), silent_log)
    answerer = make_answerer(graph, tmp_path, Config(), silent_log)
    data = answerer("explain loader", 2)
    assert "sk-proj-VIEWERSECRET1234567890abcd" not in json.dumps(data)
