"""The OpenGAP identity files, enforced.

The manifest rules asserted here were recorded by running the real validator
(`@open-gitagent/opengap` 0.5.0) against deliberately malformed manifests:
the root object rejects additional properties, `name` must match
`^[a-z][a-z0-9-]*$`, and `description` is required.
"""

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_FIELDS = {"spec_version", "name", "version", "description"}


@pytest.fixture
def manifest():
    with open(ROOT / "agent.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def test_manifest_has_exactly_four_scalar_fields(manifest):
    assert set(manifest) == REQUIRED_FIELDS
    for key, value in manifest.items():
        assert isinstance(value, (str, int, float)), f"{key} must be scalar, got {type(value)}"


def test_spec_version_is_pinned(manifest):
    assert str(manifest["spec_version"]) == "0.1.0"


def test_name_matches_the_validators_pattern(manifest):
    assert re.fullmatch(r"^[a-z][a-z0-9-]*$", manifest["name"])
    assert manifest["name"] == "orgono"


def test_soul_has_a_boundaries_section():
    soul = (ROOT / "SOUL.md").read_text(encoding="utf-8")
    assert "## Boundaries" in soul
    for claim in ("Egress is off", "never writes", "capped"):
        assert claim.lower() in soul.lower()


def _sentences(block: str) -> list[str]:
    # Strip inline code so `extract.py` is not counted as a sentence break.
    cleaned = re.sub(r"`[^`]*`", "CODE", block).strip()
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z])", cleaned)
    return [p for p in (s.strip() for s in parts) if p]


def _sections(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    current = None
    buf: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            if current:
                out[current] = "\n".join(buf).strip()
            current = line[3:].strip()
            buf = []
        elif current:
            buf.append(line)
    if current:
        out[current] = "\n".join(buf).strip()
    return out


def test_explainability_headings_are_exact():
    text = (ROOT / "EXPLAINABILITY.md").read_text(encoding="utf-8")
    assert list(_sections(text)) == ["Decision Reasoning", "Data Inputs", "Known Limitations"]


@pytest.mark.parametrize(
    "heading", ["Decision Reasoning", "Data Inputs", "Known Limitations"]
)
def test_each_explainability_section_is_exactly_two_sentences(heading):
    text = (ROOT / "EXPLAINABILITY.md").read_text(encoding="utf-8")
    body = _sections(text)[heading]
    found = _sentences(body)
    assert len(found) == 2, f"{heading}: expected 2 sentences, got {len(found)}: {found}"


def test_known_limitations_names_real_limits():
    text = (ROOT / "EXPLAINABILITY.md").read_text(encoding="utf-8")
    body = _sections(text)["Known Limitations"].lower()
    # These are limits we can demonstrate, not modest-sounding padding.
    assert "by name" in body
    assert "cross-language" in body or "type inference" in body
