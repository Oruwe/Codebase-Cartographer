"""Bounded, safe-by-default configuration for Orgono.

Every limit here has a default that cannot hang a laptop, and every switch that
sends source code off the machine or costs money defaults to OFF.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field, replace

# Languages Orgono supports. Anything else is reported as `unsupported`,
# never half-parsed. Kept in sync with orgono.app.core.languages.LANGUAGES
# by test_languages.py.
SUPPORTED_LANGUAGES = (
    "bash",
    "c",
    "c_sharp",
    "cpp",
    "go",
    "java",
    "javascript",
    "kotlin",
    "lua",
    "php",
    "python",
    "ruby",
    "rust",
    "scala",
    "sql",
    "swift",
    "typescript",
    "tsx",
)

# Directories never traversed. Vendored bundles produce noise, not architecture.
DEFAULT_EXCLUDE_DIRS = (
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    "dist",
    "build",
    "target",
    "vendor",
    "third_party",
    ".next",
    ".nuxt",
    "site-packages",
    ".orgono",
)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Limits:
    """Hard caps on extraction. Each is configurable; each default is laptop-safe."""

    max_file_bytes: int = 1_048_576  # 1 MiB. A 200MB bundle is skipped, not parsed.
    max_files: int = 5_000
    max_depth: int = 25
    max_wall_seconds: float = 120.0
    max_total_bytes: int = 268_435_456  # 256 MiB read budget across the whole run.

    @staticmethod
    def from_env() -> Limits:
        return Limits(
            max_file_bytes=_env_int("ORGONO_MAX_FILE_BYTES", 1_048_576),
            max_files=_env_int("ORGONO_MAX_FILES", 5_000),
            max_depth=_env_int("ORGONO_MAX_DEPTH", 25),
            max_wall_seconds=_env_float("ORGONO_MAX_WALL_SECONDS", 120.0),
            max_total_bytes=_env_int("ORGONO_MAX_TOTAL_BYTES", 268_435_456),
        )


@dataclass(frozen=True)
class QueryCaps:
    """Response caps for the sibling-agent query surface.

    These are enforced in code (orgono.app.core.query), never requested in a prompt.
    """

    max_nodes: int = 200
    max_edges: int = 400
    max_snippet_lines: int = 12
    max_depth: int = 5
    # A query whose selector would match more than this fraction of the graph is
    # refused as a bulk-export attempt rather than truncated.
    bulk_refusal_ratio: float = 0.5
    # ...but a graph smaller than this is never considered "bulk".
    bulk_refusal_min_nodes: int = 50

    @staticmethod
    def from_env() -> QueryCaps:
        return QueryCaps(
            max_nodes=_env_int("ORGONO_QUERY_MAX_NODES", 200),
            max_edges=_env_int("ORGONO_QUERY_MAX_EDGES", 400),
            max_snippet_lines=_env_int("ORGONO_QUERY_MAX_SNIPPET_LINES", 12),
            max_depth=_env_int("ORGONO_QUERY_MAX_DEPTH", 5),
        )


@dataclass(frozen=True)
class EgressPolicy:
    """The security boundary: the step where private source code leaves the machine.

    `enabled` is False unless explicitly switched on. Nothing in the local graph
    pipeline reads anything else from this object.
    """

    enabled: bool = False
    dry_run: bool = True
    model: str = "anthropic/claude-3.5-haiku"
    base_url: str = "https://openrouter.ai/api/v1"
    max_prompt_tokens: int = 8_000
    max_completion_tokens: int = 1_024
    max_spend_usd: float = 0.50
    timeout_seconds: float = 30.0
    redact: bool = True

    @staticmethod
    def from_env() -> EgressPolicy:
        return EgressPolicy(
            enabled=_env_bool("ORGONO_EGRESS_ENABLED", False),
            dry_run=_env_bool("ORGONO_EGRESS_DRY_RUN", True),
            model=os.environ.get("ORGONO_EGRESS_MODEL", "anthropic/claude-3.5-haiku"),
            base_url=os.environ.get("ORGONO_OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
            max_prompt_tokens=_env_int("ORGONO_MAX_PROMPT_TOKENS", 8_000),
            max_completion_tokens=_env_int("ORGONO_MAX_COMPLETION_TOKENS", 1_024),
            max_spend_usd=_env_float("ORGONO_MAX_SPEND_USD", 0.50),
            timeout_seconds=_env_float("ORGONO_EGRESS_TIMEOUT", 30.0),
            redact=not _env_bool("ORGONO_EGRESS_NO_REDACT", False),
        )


@dataclass(frozen=True)
class Config:
    limits: Limits = field(default_factory=Limits)
    caps: QueryCaps = field(default_factory=QueryCaps)
    egress: EgressPolicy = field(default_factory=EgressPolicy)
    exclude_dirs: tuple[str, ...] = DEFAULT_EXCLUDE_DIRS
    follow_symlinks: bool = False  # symlink loops are a hostile-repo failure mode

    @staticmethod
    def from_env() -> Config:
        return Config(
            limits=Limits.from_env(),
            caps=QueryCaps.from_env(),
            egress=EgressPolicy.from_env(),
        )

    def with_limits(self, **kw) -> Config:
        return replace(self, limits=replace(self.limits, **kw))

    def to_dict(self) -> dict:
        return asdict(self)


REQUIRED_PRODUCTION_SECRETS = ("ORGONO_OPENROUTER_API_KEY",)


class StartupError(RuntimeError):
    """Raised when the process refuses to boot rather than run degraded."""


def assert_bootable(env: dict[str, str] | None = None) -> None:
    """Refuse to boot in production if a required secret is missing.

    Starting in a degraded state that nobody notices is the failure this prevents.
    Only enforced when ORGONO_ENV=production; local and test runs never need a key
    because the local graph pipeline never makes a network call.
    """
    env = dict(os.environ if env is None else env)
    if env.get("ORGONO_ENV", "").strip().lower() != "production":
        return
    missing = [
        name
        for name in REQUIRED_PRODUCTION_SECRETS
        if not (env.get(name) or "").strip()
    ]
    if missing:
        raise StartupError(
            "refusing to boot: ORGONO_ENV=production but required secret(s) missing: "
            + ", ".join(sorted(missing))
        )
