"""What orgono does to the machine it is installed on.

These are the claims a user is entitled to check before letting a tool loose on
their personal files, so they are tests rather than prose.
"""

import pathlib

import orgono

PKG = pathlib.Path(orgono.__file__).resolve().parent
SOURCES = sorted(PKG.rglob("*.py"))


def _bodies():
    return {p: p.read_text(encoding="utf-8") for p in SOURCES}


def test_no_dynamic_code_execution():
    import re as _re
    # `re.compile` is regex compilation, not code execution, so it is excluded
    # explicitly rather than by dropping the `compile(` check altogether.
    banned = (
        _re.compile(r"\beval\("),
        _re.compile(r"\bexec\("),
        _re.compile(r"\bos\.system\("),
        _re.compile(r"\bos\.popen\("),
        _re.compile(r"(?<!re\.)(?<!\w)compile\(", ),
        _re.compile(r"\bpickle\.loads\b"),
        _re.compile(r"\bmarshal\.loads\b"),
    )
    offenders = []
    for path, body in _bodies().items():
        stripped = body.replace("re.compile(", "REGEX(")
        for pattern in banned:
            if pattern.search(stripped):
                offenders.append(f"{path.name}: {pattern.pattern}")
    assert not offenders, f"dynamic code execution found: {offenders}"


def test_no_shell_invocation():
    for path, body in _bodies().items():
        assert "shell=True" in body is False or "shell=True" not in body, (
            f"{path.name} invokes a shell"
        )


def test_the_only_subprocess_is_the_opengap_validator():
    """`orgono validate` shells out to the opengap binary. Nothing else may."""
    users = [p.name for p, body in _bodies().items() if "subprocess" in body]
    assert users == ["cli.py"], f"unexpected subprocess use in {users}"
    body = (PKG / "cli.py").read_text(encoding="utf-8")
    assert 'subprocess.run([exe, "validate"]' in body
    assert "shell=True" not in body


def test_no_http_client_outside_the_egress_path():
    """The local pipeline must have no way to reach the network at all."""
    allowed = {"egress.py", "cli.py"}
    for path, body in _bodies().items():
        if path.name in allowed:
            continue
        for marker in ("import requests", "urllib.request", "http.client", "socket."):
            assert marker not in body, f"{path.name} can reach the network ({marker})"


def test_requests_is_imported_lazily_not_at_module_scope():
    """Installing orgono must not pull an HTTP client into every process."""
    for name in ("egress.py", "cli.py"):
        body = (PKG / "app" / "core" / name).read_text(encoding="utf-8") if name == "egress.py" \
            else (PKG / name).read_text(encoding="utf-8")
        for line in body.splitlines():
            if line.strip().startswith(("import requests", "from requests")):
                assert line.startswith(" "), f"{name}: requests imported at module scope"


def test_importing_orgono_opens_no_network_connection(monkeypatch):
    import socket
    def boom(*a, **k):
        raise AssertionError("import-time network access")
    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    import importlib

    import orgono.cli
    importlib.reload(orgono.cli)


def test_no_writes_outside_the_target_repository(tmp_path, silent_log, monkeypatch):
    """Everything orgono writes lands in <repo>/.orgono/."""
    from orgono.app.core.agent import Cartographer
    from orgono.app.core.config import Config

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("def f(): return 1\n")

    cart = Cartographer.create(repo, Config(), silent_log)
    cart.build()

    written = {p for p in repo.rglob("*") if p.is_file()}
    outside = [p for p in written if ".orgono" not in p.parts and p.name != "a.py"]
    assert not outside, f"wrote outside .orgono/: {outside}"
    assert list(fake_home.rglob("*")) == [], "orgono wrote into the user's home directory"


def _code_only(source: str) -> str:
    """Strip comments and string literals, so prose cannot trip a code check."""
    import io
    import tokenize

    kept = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            kept.append(tok.string)
    except (tokenize.TokenError, IndentationError):
        return source
    return " ".join(kept)


def test_no_telemetry_or_auto_update_endpoints():
    """No telemetry SDK, no analytics client, no self-updater.

    Scans executable tokens rather than raw text: the word "telemetry" appearing
    in a comment is documentation, not a network call, and a check that cannot
    tell the difference is a check nobody will keep.
    """
    sdks = ("sentry_sdk", "posthog", "mixpanel", "segment_analytics",
            "amplitude", "datadog", "opentelemetry", "bugsnag", "rollbar")
    offenders = []
    for path, body in _bodies().items():
        code = _code_only(body).lower()
        for sdk in sdks:
            if sdk in code:
                offenders.append(f"{path.name}: {sdk}")
        for updater in ("urlretrieve", "check_for_update", "self_update"):
            if updater in code:
                offenders.append(f"{path.name}: {updater}")
    assert not offenders, f"telemetry or auto-update machinery found: {offenders}"


def test_no_telemetry_hosts_are_referenced_anywhere():
    """Belt and braces: no telemetry endpoint, even in a comment or docstring."""
    import re
    hosts = set()
    for _path, body in _bodies().items():
        hosts |= set(re.findall(r"https?://([A-Za-z0-9.\-]+)", body))
    for host in hosts:
        for bad in ("sentry.io", "posthog", "mixpanel", "segment.io", "amplitude",
                    "datadoghq", "google-analytics"):
            assert bad not in host, f"telemetry endpoint referenced: {host}"


def test_the_only_outbound_host_is_the_configured_egress_endpoint():
    import re
    hosts = set()
    for _path, body in _bodies().items():
        hosts |= set(re.findall(r"https?://([A-Za-z0-9.\-]+)", body))
    # github.com appears only as a documentation/referer string.
    assert hosts <= {"openrouter.ai", "github.com"}, f"unexpected hosts: {hosts}"


def test_package_declares_exactly_one_entry_point():
    root = PKG.parent
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        return
    body = pyproject.read_text(encoding="utf-8")
    scripts = body.split("[project.scripts]")[1].split("[")[0]
    entries = [ln for ln in scripts.splitlines() if "=" in ln]
    assert len(entries) == 1, f"expected one console script, found {entries}"


def test_no_build_time_code_execution():
    """No setup.py means nothing arbitrary runs at install time."""
    root = PKG.parent
    assert not (root / "setup.py").exists()
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        body = pyproject.read_text(encoding="utf-8")
        assert 'build-backend = "setuptools.build_meta"' in body
