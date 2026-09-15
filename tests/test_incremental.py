"""Incremental work is keyed on content hash, never mtime."""

import os
import time

from orgono.app.core.config import Config
from orgono.app.core.extract import extract_repo, load_cache, save_cache, sha256_bytes


def test_cache_records_content_hashes(tmp_path, golden_dir, silent_log):
    graph = extract_repo(golden_dir, Config(), silent_log)
    cache_file = tmp_path / "cache.json"
    save_cache(cache_file, graph)
    cache = load_cache(cache_file)
    assert cache, "cache must not be empty"
    for entry in cache.values():
        assert len(entry["sha256"]) == 64


def test_touching_a_file_does_not_change_its_hash(tmp_path, silent_log):
    src = tmp_path / "a.py"
    src.write_text("def f():\n    return 1\n")
    first = extract_repo(tmp_path, Config(), silent_log)
    before = first.files["a.py"].sha256

    future = time.time() + 50_000
    os.utime(src, (future, future))

    second = extract_repo(tmp_path, Config(), silent_log)
    assert second.files["a.py"].sha256 == before
    assert first.digest() == second.digest()


def test_changing_content_changes_the_hash_and_the_graph(tmp_path, silent_log):
    src = tmp_path / "a.py"
    src.write_text("def f():\n    return 1\n")
    first = extract_repo(tmp_path, Config(), silent_log)
    src.write_text("def f():\n    return 1\ndef g():\n    return f()\n")
    second = extract_repo(tmp_path, Config(), silent_log)
    assert first.files["a.py"].sha256 != second.files["a.py"].sha256
    assert first.digest() != second.digest()


def test_cache_is_reused_when_content_is_unchanged(tmp_path, silent_log):
    (tmp_path / "a.py").write_text("def f(): return 1\n")
    first = extract_repo(tmp_path, Config(), silent_log)
    cache_file = tmp_path / ".orgono" / "cache.json"
    save_cache(cache_file, first)
    cache = load_cache(cache_file)
    second = extract_repo(tmp_path, Config(), silent_log, cache=cache)
    assert second.stats["reused_from_cache"] >= 1


def test_cache_version_mismatch_is_ignored_safely(tmp_path):
    bad = tmp_path / "c.json"
    bad.write_text('{"version": "something-else", "files": {"a": {"sha256": "x"}}}')
    assert load_cache(bad) == {}


def test_corrupt_cache_does_not_raise(tmp_path):
    bad = tmp_path / "c.json"
    bad.write_text("{not json")
    assert load_cache(bad) == {}


def test_sha256_helper():
    assert len(sha256_bytes(b"abc")) == 64
