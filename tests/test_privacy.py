"""Privacy and data-handling guarantees.

orgono runs against people's private source code on their personal machines.
Every claim made about what it stores, what it exposes and what it transmits is
asserted here, because a privacy claim that is only written in a README is a
claim that will drift.

Each test below was written after finding the corresponding problem by running
the tool, not by reading it.
"""

import json
import urllib.error
import urllib.request

import pytest

from orgono.app.core.agent import Cartographer
from orgono.app.core.config import Config, EgressPolicy
from orgono.app.core.egress import build_plan, redacted_plan_for_display
from orgono.app.core.extract import extract_repo
from orgono.app.core.viz import serve

SECRETS = {
    "openai": "sk-proj-REALSECRETVALUE1234567890abcdef",
    "password": "hunter2-super-secret",
    "stripe": "sk_live_abcdefghijklmnop1234",
    "github": "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345",
    "conn": "SuperSecretPw99",
}


@pytest.fixture
def secret_repo(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "config.py").write_text(
        f'OPENAI_API_KEY = "{SECRETS["openai"]}"\n'
        f'DB_PASSWORD = "{SECRETS["password"]}"\n'
        f'STRIPE = "{SECRETS["stripe"]}"\n'
    )
    (src / "auth.py").write_text(
        "def authenticate(user):\n"
        f'    api_key = "{SECRETS["openai"]}"\n'
        f'    token = "{SECRETS["github"]}"\n'
        f'    conn = "postgres://admin:{SECRETS["conn"]}@db.internal/prod"\n'
        "    return call_api(user, api_key)\n"
        "\n\ndef call_api(u, k):\n    return None\n"
    )
    (tmp_path / ".env").write_text(
        "ORGONO_OPENROUTER_API_KEY=sk-or-v1-THISMUSTNOTLEAK123456\n"
        "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY\n"
    )
    return tmp_path


# --- data at rest -----------------------------------------------------------

def test_no_secret_value_ever_reaches_the_graph_on_disk(secret_repo, silent_log):
    cart = Cartographer.create(secret_repo, Config(), silent_log)
    cart.build()
    blob = cart.graph_path().read_text(encoding="utf-8")
    for label, secret in SECRETS.items():
        assert secret not in blob, f"{label} secret was written to graph.json"
    assert "sk-or-v1-THISMUSTNOTLEAK123456" not in blob


def test_graph_records_names_and_locations_but_never_values(secret_repo, silent_log):
    graph = extract_repo(secret_repo, Config(), silent_log)
    names = {n.name for n in graph.nodes.values()}
    assert "DB_PASSWORD" in names, "the identifier is useful and is kept"
    assert SECRETS["password"] not in json.dumps(graph.to_dict()), "the value is not"


def test_graph_json_never_contains_an_absolute_path(secret_repo, silent_log):
    """An absolute path carries the operator's OS username and directory layout,
    and graph.json is a file people share."""
    cart = Cartographer.create(secret_repo, Config(), silent_log)
    cart.build()
    payload = json.loads(cart.graph_path().read_text(encoding="utf-8"))
    assert payload["root"] == secret_repo.name
    assert "/" not in payload["root"] and "\\" not in payload["root"]
    blob = json.dumps(payload)
    assert str(secret_repo) not in blob, "the absolute repository path was serialised"


def test_output_directory_ignores_itself(secret_repo, silent_log):
    """A code map must not be committed to the user's repository by accident."""
    cart = Cartographer.create(secret_repo, Config(), silent_log)
    cart.build()
    marker = cart.out_dir() / ".gitignore"
    assert marker.is_file(), ".orgono/ must ignore itself"
    assert marker.read_text(encoding="utf-8").strip().endswith("*")


def test_dotenv_files_are_never_parsed_as_source(secret_repo, silent_log):
    graph = extract_repo(secret_repo, Config(), silent_log)
    assert not any(".env" in path for path in graph.files)


def test_symlinks_are_skipped_not_followed(tmp_path, silent_log):
    import contextlib
    import os
    (tmp_path / "real.py").write_text("def f(): return 1\n")
    with contextlib.suppress(OSError, NotImplementedError):
        os.symlink("/etc/passwd", tmp_path / "escape.py")
    graph = extract_repo(tmp_path, Config(), silent_log)
    if "escape.py" in graph.files:
        assert graph.files["escape.py"].status == "skipped"
        assert graph.files["escape.py"].reason == "symlink"


# --- data in transit --------------------------------------------------------

def test_inline_secrets_are_redacted_before_any_egress(secret_repo, silent_log):
    cart = Cartographer.create(secret_repo, Config(), silent_log)
    cart.build()
    result = cart.engine().find_symbol(
        name="authenticate", include_snippets=True, caller="test"
    )
    plan = build_plan("explain", result, cart.graph, EgressPolicy(enabled=True), api_key="k")
    blob = json.dumps(plan.to_dict())
    for label, secret in SECRETS.items():
        assert secret not in blob, f"{label} secret would have been transmitted"


def test_redaction_count_is_honest(secret_repo, silent_log):
    """Regression: snippets are redacted on read, so a second pass in build_plan
    found nothing and reported 0 redactions on a payload that had several. A
    dry-run that says "0 redactions" tells the operator their code was clean."""
    cart = Cartographer.create(secret_repo, Config(), silent_log)
    cart.build()
    result = cart.engine().find_symbol(
        name="authenticate", include_snippets=True, caller="test"
    )
    assert result.redactions >= 3, "snippet redaction must be counted where it happens"
    plan = build_plan("explain", result, cart.graph, EgressPolicy(enabled=True), api_key="k")
    assert plan.redactions >= 3, "the reported count must survive into the payload"


def test_ask_path_carries_the_redaction_count(secret_repo, silent_log):
    from orgono.app.core import ai
    cart = Cartographer.create(secret_repo, Config(), silent_log)
    cart.build()
    grounding = ai.retrieve("explain authenticate", cart.engine(), depth=2)
    merged = ai.merge_results(grounding, 200, 400)
    if any(r.redactions for r in grounding.results):
        assert merged.redactions > 0, "merging must not reset security telemetry"


def test_api_key_is_masked_in_the_inspectable_payload(secret_repo, silent_log):
    cart = Cartographer.create(secret_repo, Config(), silent_log)
    cart.build()
    result = cart.engine().find_symbol(name="call_api", caller="test")
    plan = build_plan("q", result, cart.graph, EgressPolicy(enabled=True),
                      api_key="sk-or-v1-USERSREALKEY9999")
    shown = json.dumps(redacted_plan_for_display(plan))
    assert "sk-or-v1-USERSREALKEY9999" not in shown


# --- the local viewer -------------------------------------------------------

@pytest.fixture
def running_viewer(golden_graph, silent_log):
    httpd, url = serve(golden_graph, host="127.0.0.1", port=0, log=silent_log, block=False)
    yield url.rsplit("/", 1)[0]
    httpd.shutdown()
    httpd.server_close()


@pytest.mark.parametrize("attack", [
    "../../../../etc/passwd",
    "..%2f..%2f..%2f..%2fetc%2fpasswd",
    "....//....//etc/passwd",
    "%2e%2e%2f%2e%2e%2fetc%2fpasswd",
    "..\\..\\..\\etc\\passwd",
])
def test_viewer_refuses_path_traversal(running_viewer, attack):
    try:
        with urllib.request.urlopen(f"{running_viewer}/{attack}", timeout=5) as resp:
            body = resp.read()
    except urllib.error.HTTPError as exc:
        assert exc.code in (403, 404)
        return
    assert b"root:x:0:0" not in body


def test_viewer_binds_loopback_only(golden_graph, silent_log):
    httpd, url = serve(golden_graph, host="127.0.0.1", port=0, log=silent_log, block=False)
    try:
        assert httpd.server_address[0] == "127.0.0.1"
        assert url.startswith("http://127.0.0.1:")
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_viewer_payload_carries_no_source_code(golden_graph):
    from orgono.app.core.viz import build_payload
    blob = json.dumps(build_payload(golden_graph))
    for marker in ("def ", "return ", "import sqlite3"):
        assert marker not in blob, f"viewer payload contains source code: {marker!r}"


# --- blast radius of untrusted content --------------------------------------

def test_model_output_is_never_executed_or_written():
    """Repository content reaches the model, so the model's reply is untrusted.
    It may only be displayed."""
    import pathlib

    import orgono
    body = (pathlib.Path(orgono.__file__).parent / "cli.py").read_text(encoding="utf-8")
    segment = body[body.index("def cmd_ask"):body.index("def cmd_doctor")]
    for sink in ("subprocess", "exec(", "eval(", "write_text", "os.remove"):
        assert sink not in segment, f"model output could reach {sink}"
