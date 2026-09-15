"""The terminal surface. Every command is `orgono <verb>`."""

import json

import pytest

from orgono.cli import build_parser, main


def _run(capsys, argv):
    code = main(argv)
    out = capsys.readouterr()
    return code, out.out, out.err


def test_all_commands_are_registered():
    parser = build_parser()
    actions = [a for a in parser._actions if hasattr(a, "choices") and a.choices]
    commands = set(actions[0].choices)
    assert {"map", "view", "find", "impact", "path", "file", "stats", "report",
            "tools", "auth", "models", "ask", "explain", "doctor", "validate"} <= commands


def test_map_then_query(tmp_path, capsys, golden_dir):
    import shutil
    work = tmp_path / "repo"
    shutil.copytree(golden_dir, work)
    code, out, _ = _run(capsys, ["map", "-p", str(work), "--quiet", "--json"])
    assert code == 0
    stats = json.loads(out)
    assert stats["nodes"] > 0
    assert (work / ".orgono" / "graph.json").exists()

    code, out, _ = _run(capsys, ["impact", "query_users", "-p", str(work),
                                 "--quiet", "--json", "--direction", "callers"])
    assert code == 0
    assert "find_by_email" in out


def test_find_exact(tmp_path, capsys, golden_dir):
    code, out, _ = _run(capsys, ["find", "query_users", "-p", str(golden_dir),
                                 "--quiet", "--json", "--exact"])
    assert code == 0
    payload = json.loads(out)
    assert all(n["name"] == "query_users" for n in payload["nodes"])


def test_path_command(capsys, golden_dir):
    code, out, _ = _run(capsys, ["path", "get_user_route", "query_users",
                                 "-p", str(golden_dir), "--quiet", "--json"])
    assert code == 0
    assert json.loads(out)["edges"]


def test_report_lists_unparsed_and_unsupported(capsys, golden_dir):
    code, out, _ = _run(capsys, ["report", "-p", str(golden_dir), "--quiet", "--json"])
    assert code == 0
    rows = json.loads(out)
    paths = {r["path"] for r in rows}
    assert "data.csv" in paths
    assert "src/broken.py" in paths


def test_stats_command(capsys, golden_dir):
    code, out, _ = _run(capsys, ["stats", "-p", str(golden_dir), "--quiet", "--json"])
    assert code == 0
    assert "node_kinds" in json.loads(out)


def test_tools_prints_the_manifest(capsys):
    code, out, _ = _run(capsys, ["tools"])
    assert code == 0
    assert "find_symbol" in json.loads(out)


def test_doctor_reports_healthy(capsys):
    code, out, _ = _run(capsys, ["doctor", "--no-color"])
    assert code == 0
    assert "languages" in out
    assert "egress" in out


def test_ask_answers_locally_without_network(capsys, golden_dir):
    code, out, _ = _run(capsys, ["ask", "what calls query_users?",
                                 "-p", str(golden_dir), "--quiet"])
    assert code == 0
    assert "query_users" in out
    assert "no model" in out


def test_ask_refuses_to_send_when_egress_is_off(capsys, golden_dir):
    code, _out, err = _run(capsys, ["ask", "what calls query_users?",
                                    "-p", str(golden_dir), "--quiet", "--send"])
    assert code == 2
    assert "egress is disabled" in err


def test_explain_dry_run_prints_the_payload_and_sends_nothing(capsys, golden_dir):
    code, out, _ = _run(capsys, ["explain", "what is this?", "--about", "query_users",
                                 "-p", str(golden_dir), "--quiet"])
    assert code == 0
    assert "DRY RUN" in out
    assert "chat/completions" in out


def test_auth_status_reports_no_stored_key(capsys, tmp_path, monkeypatch):
    for var in ("ORGONO_OPENROUTER_API_KEY", "OPENROUTER_API_KEY", "ORGONO_ENV_FILE"):
        monkeypatch.delenv(var, raising=False)
    code, out, _ = _run(capsys, ["auth", "status", "-p", str(tmp_path), "--no-color"])
    assert code == 0
    assert "(not set)" in out
    assert "never writes your key" in out


def test_impact_on_a_missing_symbol_is_honest(capsys, golden_dir):
    code, out, _ = _run(capsys, ["impact", "does_not_exist", "-p", str(golden_dir),
                                 "--quiet", "--json"])
    assert code == 0
    assert json.loads(out)["total_matched"] == 0


def test_unknown_command_exits_nonzero():
    with pytest.raises(SystemExit):
        main(["not-a-command"])
