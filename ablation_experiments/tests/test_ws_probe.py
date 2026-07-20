from __future__ import annotations

import sys
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from ws_probe import extract_timeline


def test_extract_timeline_tracks_first_audio_and_completion() -> None:
    metrics = extract_timeline(
        [
            {"kind": "sent", "at_ms": 0},
            {"kind": "text", "at_ms": 120},
            {"kind": "audio", "at_ms": 260},
            {"kind": "completed", "at_ms": 840},
        ]
    )

    assert metrics["t_first_token_ms"] == 120
    assert metrics["t_first_audio_ms"] == 260
    assert metrics["t_complete_ms"] == 840


def test_extract_timeline_returns_none_for_missing_audio() -> None:
    metrics = extract_timeline(
        [{"kind": "sent", "at_ms": 0}, {"kind": "completed", "at_ms": 30}]
    )

    assert metrics["t_first_audio_ms"] is None
