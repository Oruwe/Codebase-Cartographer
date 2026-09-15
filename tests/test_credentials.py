"""Key handling: read from the environment or the user's .env; never stored by us."""


import pytest

from orgono.app.core import credentials as cr


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in cr.ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("ORGONO_ENV_FILE", raising=False)


def test_parses_a_dotenv_file(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "# a comment\n"
        "export ORGONO_OPENROUTER_API_KEY=\"sk-or-v1-abc123\"\n"
        "OTHER='x y'\n"
        "malformed line\n"
    )
    values = cr.parse_env_file(env)
    assert values["ORGONO_OPENROUTER_API_KEY"] == "sk-or-v1-abc123"
    assert values["OTHER"] == "x y"


def test_resolution_order_flag_beats_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("ORGONO_OPENROUTER_API_KEY", "from-env")
    assert cr.resolve_api_key("from-flag", root=tmp_path) == "from-flag"


def test_environment_beats_dotenv(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("ORGONO_OPENROUTER_API_KEY=from-dotenv\n")
    monkeypatch.setenv("ORGONO_OPENROUTER_API_KEY", "from-env")
    assert cr.resolve_api_key(root=tmp_path) == "from-env"


def test_dotenv_is_used_when_nothing_else_is_set(tmp_path):
    (tmp_path / ".env").write_text("ORGONO_OPENROUTER_API_KEY=from-dotenv\n")
    assert cr.resolve_api_key(root=tmp_path) == "from-dotenv"
    assert ".env" in cr.key_source(root=tmp_path)


def test_openrouter_api_key_variable_is_also_accepted(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "plain")
    assert cr.resolve_api_key(root=tmp_path) == "plain"


def test_no_key_anywhere_returns_none(tmp_path):
    assert cr.resolve_api_key(root=tmp_path) is None
    assert cr.key_source(root=tmp_path) == "none"


def test_nothing_is_written_just_by_resolving(tmp_path):
    cr.resolve_api_key(root=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_save_to_env_file_is_explicit_and_gitignores(tmp_path, monkeypatch):
    monkeypatch.delenv("ORGONO_ENV_FILE", raising=False)
    target = cr.save_to_env_file("sk-or-v1-secret", root=tmp_path)
    assert target.exists()
    assert "sk-or-v1-secret" in target.read_text()
    assert oct(target.stat().st_mode)[-3:] == "600"
    assert ".env" in (tmp_path / ".gitignore").read_text()


def test_save_replaces_an_existing_value(tmp_path):
    (tmp_path / ".env").write_text("ORGONO_OPENROUTER_API_KEY=old\nKEEP=1\n")
    cr.save_to_env_file("new", root=tmp_path)
    body = (tmp_path / ".env").read_text()
    assert "new" in body
    assert "old" not in body
    assert "KEEP=1" in body


def test_empty_key_is_refused(tmp_path):
    with pytest.raises(ValueError):
        cr.save_to_env_file("   ", root=tmp_path)


def test_mask_never_reveals_the_middle():
    assert cr.mask("sk-or-v1-abcdefghijklmn") .startswith("sk-o")
    assert "abcdefghij" not in cr.mask("sk-or-v1-abcdefghijklmn")
    assert cr.mask(None) == "(not set)"


def test_missing_dotenv_is_not_an_error(tmp_path):
    assert cr.parse_env_file(tmp_path / "nope.env") == {}
