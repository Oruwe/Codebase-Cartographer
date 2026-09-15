"""Stack detection: evidence-based, never executed."""

import pytest

from orgono.app.core.stacks import MAX_MANIFEST_BYTES, detect_stacks


@pytest.fixture
def polyglot(tmp_path):
    (tmp_path / "web").mkdir()
    (tmp_path / "requirements.txt").write_text("fastapi>=0.100\nsqlalchemy==2.0.1\npsycopg2-binary\n# comment\n-e .\n")
    (tmp_path / "web" / "package.json").write_text(
        '{"dependencies":{"react":"^18","next":"^14"},"devDependencies":{"vite":"^5"}}'
    )
    (tmp_path / "go.mod").write_text("module x\n\ngithub.com/gin-gonic/gin v1.9.1\n")
    (tmp_path / "Cargo.toml").write_text('[dependencies]\ntokio = "1"\naxum = "0.7"\n')
    (tmp_path / "Gemfile").write_text('source "https://rubygems.org"\ngem "rails", "~> 7.0"\n')
    (tmp_path / "Dockerfile").write_text("FROM python:3.11\n")
    (tmp_path / "main.tf").write_text('resource "aws_s3_bucket" "b" {}\n')
    return tmp_path


def _names(report):
    return {d.name for d in report.detections}


def test_detects_runtimes(polyglot):
    names = _names(detect_stacks(polyglot))
    assert {"Python", "Node.js", "Go", "Rust", "Ruby"} <= names


def test_detects_frameworks_from_declared_dependencies(polyglot):
    names = _names(detect_stacks(polyglot))
    assert {"FastAPI", "React", "Next.js", "Gin", "Axum", "Ruby on Rails"} <= names


def test_detects_databases_and_infra(polyglot):
    names = _names(detect_stacks(polyglot))
    assert "SQLAlchemy" in names
    assert "PostgreSQL client" in names
    assert "Docker" in names
    assert "Terraform" in names


def test_every_detection_names_its_evidence(polyglot):
    for d in detect_stacks(polyglot).detections:
        assert d.evidence, f"{d.name} was detected with no evidence"
        assert "\\" not in d.evidence, "evidence paths must be POSIX"


def test_detection_is_deterministic(polyglot):
    a = detect_stacks(polyglot).to_dict()
    b = detect_stacks(polyglot).to_dict()
    assert a == b


def test_each_stack_is_reported_once(polyglot):
    (polyglot / ".github" / "workflows").mkdir(parents=True)
    (polyglot / ".github" / "workflows" / "a.yml").write_text("on: push\n")
    (polyglot / ".github" / "workflows" / "b.yml").write_text("on: push\n")
    report = detect_stacks(polyglot)
    actions = [d for d in report.detections if d.name == "GitHub Actions"]
    assert len(actions) == 1, "one detection per stack, not one per file"


def test_empty_repository_detects_nothing(tmp_path):
    assert detect_stacks(tmp_path).detections == []


def test_malformed_manifests_do_not_raise(tmp_path):
    (tmp_path / "package.json").write_text("{not valid json")
    (tmp_path / "pyproject.toml").write_text("[[[broken toml")
    report = detect_stacks(tmp_path)
    assert "Node.js" in _names(report)   # the file itself is still evidence
    assert "Python" in _names(report)


def test_vendored_directories_are_ignored(tmp_path):
    (tmp_path / "node_modules" / "dep").mkdir(parents=True)
    (tmp_path / "node_modules" / "dep" / "package.json").write_text('{"dependencies":{"react":"1"}}')
    assert "React" not in _names(detect_stacks(tmp_path))


def test_huge_manifest_is_not_read(tmp_path):
    (tmp_path / "package.json").write_text('{"dependencies":{"react":"1"}}' + " " * (MAX_MANIFEST_BYTES + 10))
    report = detect_stacks(tmp_path)
    assert "Node.js" in _names(report)     # presence still counts
    assert "React" not in _names(report)   # contents are not parsed past the bound


def test_nothing_is_executed(tmp_path, monkeypatch):
    """Detection must never run a manifest, a build tool or a package manager."""
    import subprocess
    def boom(*a, **k):
        raise AssertionError("stack detection spawned a subprocess")
    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    (tmp_path / "package.json").write_text('{"dependencies":{"react":"1"}}')
    (tmp_path / "Makefile").write_text("all:\n\techo hi\n")
    assert detect_stacks(tmp_path).detections
