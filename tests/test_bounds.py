"""Bounds: a hostile repository must terminate inside its limits.

Covers the four failure modes that hang a naive walker: a huge vendored file,
very deep nesting, a symlink loop, and a large file count.
"""

import contextlib
import os
import time

import pytest

from orgono.app.core.config import Config, Limits
from orgono.app.core.extract import extract_repo, iter_source_files
from orgono.app.core.obs import Logger


@pytest.fixture
def hostile_repo(tmp_path):
    root = tmp_path / "hostile"
    (root / "deep").mkdir(parents=True)

    # 1. A large "vendored bundle" that must be skipped, not parsed.
    big = root / "bundle.js"
    big.write_text("var x = 1;\n" * 300_000)  # ~3.3 MB

    # 2. Deep nesting well past the depth cap.
    cur = root / "deep"
    for i in range(60):
        cur = cur / f"lvl{i}"
        cur.mkdir()
        (cur / "m.py").write_text(f"def f{i}():\n    return {i}\n")

    # 3. A symlink loop.
    with contextlib.suppress(OSError, NotImplementedError):
        os.symlink(root, root / "loop", target_is_directory=True)

    # 4. Many small files.
    many = root / "many"
    many.mkdir()
    for i in range(600):
        (many / f"f{i}.py").write_text(f"def g{i}(): return {i}\n")
    return root


def test_hostile_repo_terminates_quickly(hostile_repo, silent_log):
    cfg = Config(limits=Limits(max_file_bytes=200_000, max_files=300, max_depth=6,
                               max_wall_seconds=20.0))
    started = time.perf_counter()
    graph = extract_repo(hostile_repo, cfg, silent_log)
    elapsed = time.perf_counter() - started
    assert elapsed < 20.0
    parsed = [f for f in graph.files.values() if f.status in ("parsed", "partial")]
    assert len(parsed) <= cfg.limits.max_files


def test_oversized_file_is_skipped_with_a_logged_reason(hostile_repo, silent_log):
    cfg = Config(limits=Limits(max_file_bytes=200_000, max_files=300, max_depth=6))
    graph = extract_repo(hostile_repo, cfg, silent_log)
    bundle = graph.files.get("bundle.js")
    assert bundle is not None, "the oversized file must be reported, not dropped"
    assert bundle.status == "skipped"
    assert "file_too_large" in bundle.reason


def test_depth_cap_is_enforced(hostile_repo, silent_log):
    cfg = Config(limits=Limits(max_depth=4, max_files=5000))
    files, _skipped = iter_source_files(hostile_repo, cfg, silent_log)
    for path in files:
        depth = len(path.relative_to(hostile_repo.resolve()).parts) - 1
        assert depth < 4


def test_symlink_loop_does_not_hang(hostile_repo, silent_log):
    cfg = Config(limits=Limits(max_depth=8, max_files=2000, max_wall_seconds=20.0))
    started = time.perf_counter()
    files, _ = iter_source_files(hostile_repo, cfg, silent_log)
    assert time.perf_counter() - started < 20.0
    assert len(files) <= cfg.limits.max_files


def test_file_count_cap(hostile_repo, silent_log):
    cfg = Config(limits=Limits(max_files=25, max_depth=10))
    files, skipped = iter_source_files(hostile_repo, cfg, silent_log)
    assert len(files) <= 25
    assert any(s.reason == "max_files_reached" for s in skipped)


def test_wall_clock_deadline_stops_the_run(hostile_repo):
    log = Logger(enabled=False)
    cfg = Config(limits=Limits(max_wall_seconds=0.001, max_files=2000, max_depth=10))
    graph = extract_repo(hostile_repo, cfg, log)
    assert any(f.reason == "wall_clock_exceeded" for f in graph.files.values())
