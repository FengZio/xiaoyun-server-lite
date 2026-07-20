from __future__ import annotations

import sys
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from run_experiment import create_run_directory, preflight


def test_preflight_requires_websocket_and_service_b() -> None:
    errors = preflight({"ws_url": "", "service_b_url": ""})

    assert "WS_URL" in errors
    assert "SERVICE_B_URL" in errors


def test_run_directory_isolated(tmp_path: Path) -> None:
    path = create_run_directory(tmp_path, "D0", "20260720T120000Z")

    assert path == tmp_path / "20260720T120000Z_D0"
    assert path.is_dir()
