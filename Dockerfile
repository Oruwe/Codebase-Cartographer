# Orgono - deterministic codebase cartographer.
#
# The image contains no secrets. Every secret is named in deploy/blueprint.yaml
# and supplied at runtime; startup refuses to boot in production if a required
# one is missing (see orgono.app.core.config.assert_bootable).
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first so the layer caches independently of source changes.
COPY pyproject.toml README.md ./
COPY orgono ./orgono
RUN pip install --no-cache-dir .

# Run as an unprivileged user. The container only ever reads the repository it
# is pointed at and writes into that repository's .orgono/ directory.
RUN useradd --create-home --uid 10001 orgono
USER orgono

# Safe defaults, restated explicitly so they survive a base-image change.
ENV ORGONO_EGRESS_ENABLED=0 \
    ORGONO_EGRESS_DRY_RUN=1 \
    ORGONO_MAX_FILES=5000 \
    ORGONO_MAX_FILE_BYTES=1048576 \
    ORGONO_MAX_WALL_SECONDS=120

# Secrets are NEVER baked in. Named, unvalued, supplied at runtime:
#   ORGONO_OPENROUTER_API_KEY   (only needed when egress is switched on)
#   ORGONO_ENV=production       (makes the boot check strict)

EXPOSE 7373

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD ["orgono", "doctor"]

ENTRYPOINT ["orgono"]
CMD ["--help"]
