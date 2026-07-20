from __future__ import annotations

import sys
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import service_probe


def test_poll_analysis_marks_timeout(monkeypatch) -> None:
    monkeypatch.setattr(service_probe, "fetch_analysis", lambda *_: {"state": "pending"})

    result = service_probe.poll_analysis("http://service-b", "music-1", deadline_s=0)

    assert result["status"] == "timeout"
    assert result["analysis_latency_ms"] is None
