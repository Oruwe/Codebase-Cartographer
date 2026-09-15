"""The manifest is four scalar fields, so the real contract lives here.

A claim nobody tests is a claim that will drift. These tests assert the tool
surface, the limits and the egress rules against the code itself.
"""

import inspect

import pytest

from orgono.app.core.agent import TOOL_SURFACE, Cartographer, tool_manifest
from orgono.app.core.config import Config, EgressPolicy, Limits, QueryCaps
from orgono.app.core.query import QueryEngine, QueryRefused

EXPECTED_TOOLS = {"find_symbol", "impact", "path_between", "file_summary", "stats"}


def test_tool_surface_is_exactly_what_we_document():
    assert set(TOOL_SURFACE) == EXPECTED_TOOLS


@pytest.mark.parametrize("tool", sorted(EXPECTED_TOOLS - {"stats"}))
def test_every_declared_tool_exists_on_the_engine(tool):
    method = getattr(QueryEngine, tool, None)
    assert callable(method), f"{tool} is declared but not implemented"
    params = set(inspect.signature(method).parameters) - {"self", "caller"}
    declared = set(TOOL_SURFACE[tool]["params"])
    assert declared == params, f"{tool}: manifest {declared} != signature {params}"


def test_there_is_no_free_form_tool():
    """A free-form instruction parameter would be the injection vector."""
    for name, spec in TOOL_SURFACE.items():
        for param in spec["params"]:
            assert param not in {"prompt", "instruction", "query_text", "command", "code"}, (
                f"{name} exposes a free-form parameter"
            )


def test_unknown_tool_is_refused(golden_dir, silent_log):
    cart = Cartographer.create(golden_dir, Config(), silent_log)
    with pytest.raises(QueryRefused, match="unknown tool"):
        cart.call_tool("summarise_entire_codebase", caller="attacker")


def test_unexpected_parameters_are_refused(golden_dir, silent_log):
    cart = Cartographer.create(golden_dir, Config(), silent_log)
    with pytest.raises(QueryRefused, match="unexpected parameter"):
        cart.call_tool("find_symbol", caller="attacker", name="x", exfiltrate=True)


def test_manifest_is_serializable():
    assert "find_symbol" in tool_manifest()


# --- the limits we claim ---------------------------------------------------

def test_default_limits_cannot_hang_a_laptop():
    limits = Limits()
    assert limits.max_file_bytes <= 2_000_000
    assert limits.max_files <= 20_000
    assert limits.max_depth <= 50
    assert limits.max_wall_seconds <= 600


def test_default_query_caps_are_bounded():
    caps = QueryCaps()
    assert caps.max_nodes <= 1000
    assert caps.max_edges <= 5000
    assert caps.max_snippet_lines <= 50


def test_egress_defaults_are_off():
    policy = EgressPolicy()
    assert policy.enabled is False
    assert policy.dry_run is True
    assert policy.redact is True
    assert policy.max_spend_usd <= 5.0


def test_writes_stay_inside_the_repository(tmp_path, silent_log):
    cart = Cartographer.create(tmp_path, Config(), silent_log)
    graph = cart.build()
    assert cart.graph_path().resolve().is_relative_to(tmp_path.resolve())
    assert graph is not None


def test_local_pipeline_does_not_import_requests(golden_dir, silent_log):
    """The local path must have no HTTP dependency at all."""
    import sys
    for mod in list(sys.modules):
        if mod == "requests":
            del sys.modules[mod]
    from orgono.app.core.extract import extract_repo
    extract_repo(golden_dir, Config(), silent_log)
    assert "requests" not in sys.modules
