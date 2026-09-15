"""Packaging: what a real user actually receives.

An editable install (`pip install -e .`) reads straight from the source tree and
therefore cannot catch a missing package-data entry. These tests assert against
the installed package instead, which is the thing users get.
"""

import pathlib

import orgono
from orgono.app.core.viz import WEB_ROOT

REQUIRED_WEB_ASSETS = (
    "index.html",
    "app.js",
    "style.css",
    "vendor/three.min.js",
    "vendor/three.LICENSE",
)


def test_web_root_resolves_inside_the_installed_package():
    pkg = pathlib.Path(orgono.__file__).resolve().parent
    assert WEB_ROOT.resolve() == (pkg / "web").resolve()
    assert WEB_ROOT.is_dir()


def test_every_web_asset_the_viewer_needs_is_present():
    missing = [a for a in REQUIRED_WEB_ASSETS if not (WEB_ROOT / a).is_file()]
    assert not missing, f"assets missing from the package: {missing}"


def test_vendored_three_js_ships_its_license():
    """three.js is MIT: redistributing it without its licence is a compliance bug."""
    licence = WEB_ROOT / "vendor" / "three.LICENSE"
    assert licence.is_file(), "three.LICENSE must ship alongside three.min.js"
    body = licence.read_text(encoding="utf-8")
    assert "MIT" in body or "Permission is hereby granted" in body


def test_package_data_declaration_covers_the_vendor_directory():
    """A `vendor/*.js` glob silently drops the licence file; guard the fix."""
    root = pathlib.Path(orgono.__file__).resolve().parents[1]
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        return  # installed from a wheel; the asset tests above already cover it
    body = pyproject.read_text(encoding="utf-8")
    assert '"vendor/*"' in body, "package-data must ship all of vendor/, not just *.js"


def test_console_entry_point_is_declared():
    root = pathlib.Path(orgono.__file__).resolve().parents[1]
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        return
    body = pyproject.read_text(encoding="utf-8")
    assert "orgono = \"orgono.cli:main\"" in body


def test_version_is_consistent():
    root = pathlib.Path(orgono.__file__).resolve().parents[1]
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        return
    body = pyproject.read_text(encoding="utf-8")
    assert f'version = "{orgono.__version__}"' in body
