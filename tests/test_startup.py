"""Production startup refuses to boot degraded, rather than half-working."""

import pytest

from orgono.app.core.config import Config, Limits, StartupError, assert_bootable


def test_local_runs_never_require_a_secret(monkeypatch):
    monkeypatch.delenv("ORGONO_ENV", raising=False)
    assert_bootable({})  # must not raise


def test_production_without_the_secret_refuses_to_boot():
    with pytest.raises(StartupError, match="refusing to boot"):
        assert_bootable({"ORGONO_ENV": "production"})


def test_production_with_the_secret_boots():
    assert_bootable({"ORGONO_ENV": "production",
                     "ORGONO_OPENROUTER_API_KEY": "sk-or-v1-x"})


def test_blank_secret_counts_as_missing():
    with pytest.raises(StartupError):
        assert_bootable({"ORGONO_ENV": "production", "ORGONO_OPENROUTER_API_KEY": "   "})


def test_limits_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("ORGONO_MAX_FILES", "42")
    assert Limits.from_env().max_files == 42


def test_invalid_env_values_fall_back_to_defaults(monkeypatch):
    monkeypatch.setenv("ORGONO_MAX_FILES", "not-a-number")
    assert Limits.from_env().max_files == Limits().max_files
    monkeypatch.setenv("ORGONO_MAX_FILES", "-5")
    assert Limits.from_env().max_files == Limits().max_files


def test_egress_env_flag_is_opt_in(monkeypatch):
    monkeypatch.delenv("ORGONO_EGRESS_ENABLED", raising=False)
    assert Config.from_env().egress.enabled is False
    monkeypatch.setenv("ORGONO_EGRESS_ENABLED", "1")
    assert Config.from_env().egress.enabled is True


def test_with_limits_returns_a_new_config():
    cfg = Config()
    tighter = cfg.with_limits(max_files=7)
    assert tighter.limits.max_files == 7
    assert cfg.limits.max_files != 7
