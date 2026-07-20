from __future__ import annotations

import sys
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from aggregate import aggregate_voice


def test_aggregate_voice_reports_first_audio_and_completion() -> None:
    result = aggregate_voice(
        [{"status": "ok", "t_first_audio_ms": 200, "t_complete_ms": 700}]
    )

    assert result["t_first_audio_p50_ms"] == 200
    assert result["t_complete_p50_ms"] == 700
