"""API key resolution.

Orgono does not persist your API key. It reads one, in this order:

    1. --api-key on the command line
    2. the process environment (ORGONO_OPENROUTER_API_KEY, then OPENROUTER_API_KEY)
    3. a .env file that YOU maintain (repo root, or ORGONO_ENV_FILE)

Nothing is written to disk unless you explicitly ask with
`orgono auth login --save-env`, which appends to your .env and makes sure .env
is git-ignored. A key held in memory for one command is the default.

Having a key available never enables egress on its own: that is a separate,
explicit switch.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path

ENV_VARS = ("ORGONO_OPENROUTER_API_KEY", "OPENROUTER_API_KEY")
DEFAULT_ENV_FILENAME = ".env"


def env_file_candidates(root: str | Path = ".") -> list[Path]:
    """.env files consulted, most specific first."""
    out: list[Path] = []
    override = os.environ.get("ORGONO_ENV_FILE")
    if override:
        out.append(Path(override))
    out.append(Path(root) / DEFAULT_ENV_FILENAME)
    out.append(Path.home() / ".orgono" / DEFAULT_ENV_FILENAME)
    seen: set[str] = set()
    unique: list[Path] = []
    for p in out:
        key = str(p)
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


def parse_env_file(path: str | Path) -> dict[str, str]:
    """Minimal .env parser: KEY=VALUE, '#' comments, optional quotes, `export`.

    Deliberately dependency-free and forgiving; a malformed line is skipped
    rather than raising, because a broken .env should not break `orgono map`.
    """
    data: dict[str, str] = {}
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return data
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            data[key] = value
    return data


def resolve_api_key(explicit: str | None = None, root: str | Path = ".") -> str | None:
    if explicit and explicit.strip():
        return explicit.strip()
    for var in ENV_VARS:
        value = os.environ.get(var, "").strip()
        if value:
            return value
    for candidate in env_file_candidates(root):
        if candidate.exists():
            values = parse_env_file(candidate)
            for var in ENV_VARS:
                if values.get(var, "").strip():
                    return values[var].strip()
    return None


def key_source(explicit: str | None = None, root: str | Path = ".") -> str:
    if explicit and explicit.strip():
        return "--api-key flag"
    for var in ENV_VARS:
        if os.environ.get(var, "").strip():
            return f"environment ({var})"
    for candidate in env_file_candidates(root):
        if candidate.exists():
            values = parse_env_file(candidate)
            for var in ENV_VARS:
                if values.get(var, "").strip():
                    return f"{candidate} ({var})"
    return "none"


def save_to_env_file(key: str, root: str | Path = ".", var: str = ENV_VARS[0]) -> Path:
    """Append the key to the project's .env, only when explicitly requested.

    Also ensures .env is git-ignored, because the most common way a key leaks is
    a .env that was never added to .gitignore.
    """
    key = (key or "").strip()
    if not key:
        raise ValueError("refusing to write an empty API key")
    target = Path(os.environ.get("ORGONO_ENV_FILE") or (Path(root) / DEFAULT_ENV_FILENAME))
    target.parent.mkdir(parents=True, exist_ok=True)

    existing = parse_env_file(target) if target.exists() else {}
    lines: list[str] = []
    if target.exists():
        lines = target.read_text(encoding="utf-8").splitlines()
    if var in existing:
        lines = [
            (f"{var}={key}" if line.strip().split("=", 1)[0].replace("export ", "").strip() == var else line)
            for line in lines
        ]
    else:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("# added by `orgono auth login --save-env`")
        lines.append(f"{var}={key}")
    target.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")
    with contextlib.suppress(OSError):
        os.chmod(target, 0o600)
    _ensure_gitignored(Path(root), target.name)
    return target


def _ensure_gitignored(root: Path, filename: str) -> None:
    gitignore = root / ".gitignore"
    try:
        if gitignore.exists():
            body = gitignore.read_text(encoding="utf-8")
            if any(line.strip() in (filename, f"/{filename}") for line in body.splitlines()):
                return
            body = body.rstrip("\n") + f"\n{filename}\n"
        else:
            body = f"{filename}\n"
        gitignore.write_text(body, encoding="utf-8")
    except OSError:
        pass


def mask(key: str | None) -> str:
    if not key:
        return "(not set)"
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:4]}…{key[-4:]} ({len(key)} chars)"
