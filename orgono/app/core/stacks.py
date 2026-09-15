"""Stack detection: what a repository is built with, not just what it is written in.

Deterministic and evidence-based. Every detection names the file that proved it,
so nothing here is a guess -- if orgono says "Django", it can point at the line
in the manifest that said so. Manifests are read with the same size bound as
source files; nothing is executed, and no lockfile is resolved over a network.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

# tomllib landed in Python 3.11; orgono supports 3.10, where the third-party
# `tomli` provides the same API. Without this, importing this module raises
# ModuleNotFoundError on every 3.10 install -- which a 3.11 dev environment can
# never reproduce, and CI caught on Linux, macOS and Windows alike.
try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on Python 3.10 in CI
    import tomli as tomllib

MAX_MANIFEST_BYTES = 512_000


@dataclass(frozen=True, order=True)
class Detection:
    name: str
    category: str  # runtime | framework | build | infra | ci | package-manager | database
    evidence: str  # the file that proved it
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# filename -> (stack name, category)
MANIFESTS: dict[str, tuple[str, str]] = {
    "package.json": ("Node.js", "runtime"),
    "pyproject.toml": ("Python", "runtime"),
    "requirements.txt": ("Python", "runtime"),
    "Pipfile": ("Python", "runtime"),
    "setup.py": ("Python", "runtime"),
    "go.mod": ("Go", "runtime"),
    "Cargo.toml": ("Rust", "runtime"),
    "pom.xml": ("Maven", "build"),
    "build.gradle": ("Gradle", "build"),
    "build.gradle.kts": ("Gradle", "build"),
    "Gemfile": ("Ruby", "runtime"),
    "composer.json": ("PHP / Composer", "runtime"),
    "build.sbt": ("sbt (Scala)", "build"),
    "Package.swift": ("Swift Package Manager", "build"),
    "CMakeLists.txt": ("CMake", "build"),
    "Makefile": ("Make", "build"),
    "Dockerfile": ("Docker", "infra"),
    "docker-compose.yml": ("Docker Compose", "infra"),
    "docker-compose.yaml": ("Docker Compose", "infra"),
    "Chart.yaml": ("Helm", "infra"),
    "Vagrantfile": ("Vagrant", "infra"),
    "Procfile": ("Procfile deploy", "infra"),
    "netlify.toml": ("Netlify", "infra"),
    "vercel.json": ("Vercel", "infra"),
    "render.yaml": ("Render", "infra"),
    "fly.toml": ("Fly.io", "infra"),
    "serverless.yml": ("Serverless Framework", "infra"),
    "package-lock.json": ("npm", "package-manager"),
    "yarn.lock": ("Yarn", "package-manager"),
    "pnpm-lock.yaml": ("pnpm", "package-manager"),
    "poetry.lock": ("Poetry", "package-manager"),
    "uv.lock": ("uv", "package-manager"),
    "Cargo.lock": ("Cargo", "package-manager"),
    "Gemfile.lock": ("Bundler", "package-manager"),
}

# directory -> (stack name, category)
DIR_MARKERS: dict[str, tuple[str, str]] = {
    ".github/workflows": ("GitHub Actions", "ci"),
    ".gitlab-ci.yml": ("GitLab CI", "ci"),
    ".circleci": ("CircleCI", "ci"),
    "k8s": ("Kubernetes", "infra"),
    "kubernetes": ("Kubernetes", "infra"),
    "migrations": ("Database migrations", "database"),
    "terraform": ("Terraform", "infra"),
}

# dependency name -> (framework, category). Matched against declared deps.
DEPENDENCY_SIGNALS: dict[str, tuple[str, str]] = {
    # JavaScript / TypeScript
    "react": ("React", "framework"),
    "next": ("Next.js", "framework"),
    "vue": ("Vue", "framework"),
    "nuxt": ("Nuxt", "framework"),
    "svelte": ("Svelte", "framework"),
    "@angular/core": ("Angular", "framework"),
    "express": ("Express", "framework"),
    "fastify": ("Fastify", "framework"),
    "nestjs": ("NestJS", "framework"),
    "@nestjs/core": ("NestJS", "framework"),
    "electron": ("Electron", "framework"),
    "vite": ("Vite", "build"),
    "webpack": ("webpack", "build"),
    "jest": ("Jest", "build"),
    "prisma": ("Prisma", "database"),
    "typeorm": ("TypeORM", "database"),
    "mongoose": ("Mongoose / MongoDB", "database"),
    "pg": ("PostgreSQL client", "database"),
    # Python
    "django": ("Django", "framework"),
    "flask": ("Flask", "framework"),
    "fastapi": ("FastAPI", "framework"),
    "starlette": ("Starlette", "framework"),
    "tornado": ("Tornado", "framework"),
    "sqlalchemy": ("SQLAlchemy", "database"),
    "psycopg": ("PostgreSQL client", "database"),
    "psycopg2": ("PostgreSQL client", "database"),
    "pymongo": ("MongoDB client", "database"),
    "redis": ("Redis client", "database"),
    "celery": ("Celery", "framework"),
    "pandas": ("pandas", "framework"),
    "numpy": ("NumPy", "framework"),
    "torch": ("PyTorch", "framework"),
    "tensorflow": ("TensorFlow", "framework"),
    "scikit-learn": ("scikit-learn", "framework"),
    "transformers": ("Hugging Face Transformers", "framework"),
    "langchain": ("LangChain", "framework"),
    "pytest": ("pytest", "build"),
    # Go / Rust / Ruby / PHP / JVM
    "github.com/gin-gonic/gin": ("Gin", "framework"),
    "github.com/labstack/echo": ("Echo", "framework"),
    "github.com/gofiber/fiber": ("Fiber", "framework"),
    "actix-web": ("Actix Web", "framework"),
    "axum": ("Axum", "framework"),
    "rocket": ("Rocket", "framework"),
    "tokio": ("Tokio", "framework"),
    "serde": ("Serde", "framework"),
    "rails": ("Ruby on Rails", "framework"),
    "sinatra": ("Sinatra", "framework"),
    "laravel/framework": ("Laravel", "framework"),
    "symfony/symfony": ("Symfony", "framework"),
    "org.springframework.boot": ("Spring Boot", "framework"),
    "spring-boot-starter": ("Spring Boot", "framework"),
}


@dataclass
class StackReport:
    detections: list[Detection] = field(default_factory=list)

    def add(self, name: str, category: str, evidence: str, detail: str = "") -> None:
        # One detection per (name, category): the first piece of evidence wins,
        # so "GitHub Actions" is not reported once per workflow file.
        if any(d.name == name and d.category == category for d in self.detections):
            return
        self.detections.append(
            Detection(name=name, category=category, evidence=evidence, detail=detail)
        )

    def sorted(self) -> list[Detection]:
        return sorted(self.detections, key=lambda d: (d.category, d.name, d.evidence))

    def by_category(self) -> dict[str, list[Detection]]:
        out: dict[str, list[Detection]] = {}
        for d in self.sorted():
            out.setdefault(d.category, []).append(d)
        return {k: out[k] for k in sorted(out)}

    def to_dict(self) -> dict:
        return {
            "detections": [d.to_dict() for d in self.sorted()],
            "categories": {k: [d.name for d in v] for k, v in self.by_category().items()},
        }


def _read_text(path: Path) -> str | None:
    try:
        if path.stat().st_size > MAX_MANIFEST_BYTES:
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _match_dependencies(names: list[str], evidence: str, report: StackReport) -> None:
    for raw in names:
        key = raw.strip().lower()
        if not key:
            continue
        hit = DEPENDENCY_SIGNALS.get(key)
        if hit is None:
            # Allow scoped/suffixed forms such as "django-rest-framework".
            for signal, value in DEPENDENCY_SIGNALS.items():
                if key == signal or key.startswith(signal + "-") or key.startswith(signal + "/"):
                    hit = value
                    break
        if hit:
            report.add(hit[0], hit[1], evidence, detail=raw)


def _deps_from_package_json(text: str) -> list[str]:
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return []
    names: list[str] = []
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        section = data.get(key)
        if isinstance(section, dict):
            names.extend(section.keys())
    return names


def _deps_from_pyproject(text: str) -> list[str]:
    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError):
        return []
    names: list[str] = []
    project = data.get("project", {})
    if isinstance(project, dict):
        for spec in project.get("dependencies", []) or []:
            if isinstance(spec, str):
                names.append(re.split(r"[<>=!\[~; ]", spec, maxsplit=1)[0])
        optional = project.get("optional-dependencies", {})
        if isinstance(optional, dict):
            for group in optional.values():
                for spec in group or []:
                    if isinstance(spec, str):
                        names.append(re.split(r"[<>=!\[~; ]", spec, maxsplit=1)[0])
    poetry = data.get("tool", {}).get("poetry", {})
    if isinstance(poetry, dict) and isinstance(poetry.get("dependencies"), dict):
        names.extend(poetry["dependencies"].keys())
    return names


def _deps_from_requirements(text: str) -> list[str]:
    names = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "-")):
            continue
        names.append(re.split(r"[<>=!\[~; ]", line, maxsplit=1)[0])
    return names


def _deps_from_go_mod(text: str) -> list[str]:
    return re.findall(r"^\s*([\w./-]+)\s+v[\d]", text, flags=re.M)


def _deps_from_cargo(text: str) -> list[str]:
    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError):
        return []
    names = []
    for key in ("dependencies", "dev-dependencies"):
        section = data.get(key)
        if isinstance(section, dict):
            names.extend(section.keys())
    return names


def _deps_from_gemfile(text: str) -> list[str]:
    return re.findall(r"""^\s*gem\s+['"]([^'"]+)['"]""", text, flags=re.M)


def _deps_from_composer(text: str) -> list[str]:
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return []
    names = []
    for key in ("require", "require-dev"):
        section = data.get(key)
        if isinstance(section, dict):
            names.extend(section.keys())
    return names


_DEP_READERS = {
    "package.json": _deps_from_package_json,
    "pyproject.toml": _deps_from_pyproject,
    "requirements.txt": _deps_from_requirements,
    "go.mod": _deps_from_go_mod,
    "Cargo.toml": _deps_from_cargo,
    "Gemfile": _deps_from_gemfile,
    "composer.json": _deps_from_composer,
}


def detect_stacks(root: str | Path, max_depth: int = 3) -> StackReport:
    """Detect the stacks a repository uses. Reads manifests only; executes nothing."""
    root = Path(root).resolve()
    report = StackReport()
    if not root.is_dir():
        return report

    skip = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build",
            "target", "vendor", ".orgono", ".tox"}

    for path in sorted(root.rglob("*")):
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        if any(part in skip for part in rel.parts):
            continue
        if len(rel.parts) > max_depth:
            continue
        rel_posix = rel.as_posix()

        if path.is_dir():
            for marker, (name, category) in DIR_MARKERS.items():
                if rel_posix == marker or rel_posix.endswith("/" + marker):
                    report.add(name, category, rel_posix)
            continue

        for marker, (name, category) in DIR_MARKERS.items():
            if rel_posix == marker:
                report.add(name, category, rel_posix)

        hit = MANIFESTS.get(path.name)
        if hit:
            report.add(hit[0], hit[1], rel_posix)
            reader = _DEP_READERS.get(path.name)
            if reader is not None:
                text = _read_text(path)
                if text:
                    _match_dependencies(reader(text), rel_posix, report)

        if path.suffix == ".tf":
            report.add("Terraform", "infra", rel_posix)
        if path.name.endswith((".yml", ".yaml")) and rel_posix.startswith(".github/workflows/"):
            report.add("GitHub Actions", "ci", rel_posix)

    return report
