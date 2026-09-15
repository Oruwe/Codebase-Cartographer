"""Observability: single-line JSON to stdout, trace_id threaded through a run.

Two rules this module exists to keep:
  1. Every emitted record passes through redaction first.
  2. A write that logs something already done must never raise. A failed audit
     line does not get to fail the run that succeeded.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, TextIO

from .redact import redact_obj

_LEVELS = {"debug": 10, "info": 20, "warn": 30, "error": 40}


def new_trace_id() -> str:
    return uuid.uuid4().hex[:16]


@dataclass
class Logger:
    """Structured logger. One JSON object per line, no multi-line records."""

    trace_id: str = field(default_factory=new_trace_id)
    stream: TextIO | None = None
    level: str = "info"
    enabled: bool = True
    _timings: dict[str, float] = field(default_factory=dict)
    records: list[dict] = field(default_factory=list)
    capture: bool = False

    def __post_init__(self) -> None:
        env_level = os.environ.get("ORGONO_LOG_LEVEL", "").strip().lower()
        if env_level in _LEVELS:
            self.level = env_level
        if os.environ.get("ORGONO_LOG", "").strip().lower() in {"0", "off", "none"}:
            self.enabled = False

    # -- emission -------------------------------------------------------
    def emit(self, event: str, level: str = "info", **fields: Any) -> None:
        """Emit one redacted JSON line. Never raises."""
        try:
            if not self.enabled or _LEVELS.get(level, 20) < _LEVELS.get(self.level, 20):
                return
            record = {
                "ts": round(time.time(), 6),
                "level": level,
                "event": event,
                "trace_id": self.trace_id,
            }
            record.update(fields)
            safe = redact_obj(record)
            if self.capture:
                self.records.append(safe)
            stream = self.stream or sys.stdout
            stream.write(json.dumps(safe, sort_keys=True, default=str) + "\n")
            stream.flush()
        except Exception:  # noqa: BLE001 - an audit line must never fail the run
            return

    def info(self, event: str, **f: Any) -> None:
        self.emit(event, "info", **f)

    def warn(self, event: str, **f: Any) -> None:
        self.emit(event, "warn", **f)

    def error(self, event: str, **f: Any) -> None:
        self.emit(event, "error", **f)

    def debug(self, event: str, **f: Any) -> None:
        self.emit(event, "debug", **f)

    # -- timings --------------------------------------------------------
    @contextmanager
    def stage(self, name: str, **fields: Any):
        """Time a stage and record it. Exceptions inside propagate; logging never adds one."""
        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed_ms = round((time.perf_counter() - start) * 1000, 3)
            with contextlib.suppress(Exception):
                self._timings[name] = self._timings.get(name, 0.0) + elapsed_ms
            self.emit("stage.complete", "info", stage=name, ms=elapsed_ms, **fields)

    @property
    def timings(self) -> dict[str, float]:
        return dict(sorted(self._timings.items()))


NULL_LOGGER = Logger(enabled=False)
