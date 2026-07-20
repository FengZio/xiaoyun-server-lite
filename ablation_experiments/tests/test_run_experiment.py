from __future__ import annotations

import sys
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import run_experiment
from run_experiment import create_run_directory, preflight


def test_preflight_requires_websocket_and_service_b() -> None:
    errors = preflight({"ws_url": "", "service_b_url": ""})

    assert "WS_URL" in errors
    assert "SERVICE_B_URL" in errors


def test_run_directory_isolated(tmp_path: Path) -> None:
    path = create_run_directory(tmp_path, "D0", "20260720T120000Z")

    assert path == tmp_path / "20260720T120000Z_D0"
    assert path.is_dir()


def test_run_group_writes_snapshot_and_records(tmp_path: Path, monkeypatch) -> None:
    matrix_path = tmp_path / "groups.yaml"
    matrix_path.write_text("baseline: {}\ngroups:\n  D0:\n    overrides: {}\n", encoding="utf-8")
    cases_path = tmp_path / "cases.jsonl"
    cases_path.write_text('{"case_id":"c1","input_text":"hello"}\n', encoding="utf-8")
    monkeypatch.setattr(run_experiment, "run_ws_case", lambda *_: {"status": "ok", "t_complete_ms": 10})

    run_dir = run_experiment.run_group("D0", {"ws_url": "ws://test", "request_timeout_s": 1}, matrix_path, cases_path, tmp_path, "run")

    assert (run_dir / "config_snapshot.json").is_file()
    assert '"group_id":"D0"' in (run_dir / "ws_records.jsonl").read_text(encoding="utf-8")


def test_main_run_dispatches_group(tmp_path: Path, monkeypatch) -> None:
    runtime_path = tmp_path / "runtime.yaml"
    runtime_path.write_text("ws_url: ws://test\nservice_b_url: http://service-b\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["run_experiment.py", "run", "--runtime", str(runtime_path), "--group", "D0"])
    monkeypatch.setattr(run_experiment, "run_group", lambda *_: tmp_path / "run")

    assert run_experiment.main() == 0
