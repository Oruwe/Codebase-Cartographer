"""Logging: single-line JSON, redacted, and never the cause of a failure."""

import io
import json

from orgono.app.core.obs import Logger, new_trace_id


def test_records_are_single_line_json():
    buf = io.StringIO()
    log = Logger(stream=buf)
    log.info("thing.happened", count=3, path="a/b.py")
    lines = buf.getvalue().strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["event"] == "thing.happened"
    assert record["count"] == 3
    assert "trace_id" in record


def test_trace_id_is_threaded_through_a_run():
    buf = io.StringIO()
    log = Logger(trace_id="abc123", stream=buf)
    log.info("one")
    log.warn("two")
    ids = {json.loads(line)["trace_id"] for line in buf.getvalue().strip().splitlines()}
    assert ids == {"abc123"}


def test_secrets_are_redacted_before_emission():
    buf = io.StringIO()
    log = Logger(stream=buf)
    log.info("config.loaded", api_key="sk-proj-abcdefghijklmnop1234567890",
             note="password: swordfish123")
    out = buf.getvalue()
    assert "sk-proj-abcdefghijklmnop1234567890" not in out
    assert "swordfish123" not in out
    assert "[REDACTED]" in out


def test_a_failing_write_never_raises():
    class Exploding(io.StringIO):
        def write(self, *a, **k):
            raise OSError("disk gone")

    log = Logger(stream=Exploding())
    log.info("this.must.not.raise", x=1)  # would fail the run if it raised


def test_unserializable_payload_never_raises():
    buf = io.StringIO()
    log = Logger(stream=buf)
    log.info("weird", obj=object())


def test_stage_timings_are_recorded():
    buf = io.StringIO()
    log = Logger(stream=buf)
    with log.stage("parse", files=2):
        pass
    assert "parse" in log.timings
    assert log.timings["parse"] >= 0


def test_stage_still_logs_when_the_body_raises():
    buf = io.StringIO()
    log = Logger(stream=buf)
    try:
        with log.stage("boom"):
            raise ValueError("inner")
    except ValueError:
        pass
    assert "boom" in log.timings


def test_levels_are_respected():
    buf = io.StringIO()
    log = Logger(stream=buf, level="warn")
    log.debug("hidden")
    log.info("also.hidden")
    log.error("shown")
    events = [json.loads(line)["event"] for line in buf.getvalue().strip().splitlines()]
    assert events == ["shown"]


def test_trace_ids_are_unique():
    assert new_trace_id() != new_trace_id()


def test_extraction_threads_one_trace_id(golden_dir):
    buf = io.StringIO()
    log = Logger(stream=buf, trace_id="run-42")
    from orgono.app.core.config import Config
    from orgono.app.core.extract import extract_repo
    extract_repo(golden_dir, Config(), log)
    records = [json.loads(line) for line in buf.getvalue().strip().splitlines()]
    assert records
    assert {r["trace_id"] for r in records} == {"run-42"}
    assert any(r["event"] == "extract.complete" for r in records)
    assert any(r["event"] == "stage.complete" for r in records)
