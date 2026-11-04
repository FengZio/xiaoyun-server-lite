from __future__ import annotations

import sys
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from ws_probe import extract_timeline, is_audio_message


def test_is_audio_message_recognizes_uniapp_reply_audio() -> None:
    assert is_audio_message({"type": "reply_audio", "audio_url": "http://example.test/a.mp3"})
    assert not is_audio_message({"type": "reply", "state": "end"})


def test_extract_timeline_tracks_first_audio_and_completion() -> None:
    metrics = extract_timeline(
        [
            {"kind": "sent", "at_ms": 0},
            {"kind": "text", "at_ms": 120},
            {"kind": "audio", "at_ms": 260},
            {"kind": "completed", "at_ms": 840},
        ]
    )

    assert metrics["t_first_visible_reply_ms"] == 120
    assert metrics["t_first_audio_ms"] == 260
    assert metrics["t_reply_complete_ms"] == 840


def test_extract_timeline_reports_audio_chunk_continuity() -> None:
    metrics = extract_timeline(
        [
            {"kind": "sent", "at_ms": 0},
            {"kind": "audio", "at_ms": 200},
            {"kind": "audio", "at_ms": 460},
            {"kind": "audio", "at_ms": 610},
            {"kind": "completed", "at_ms": 900},
        ]
    )

    assert metrics["audio_chunk_count"] == 3
    assert metrics["audio_gap_max_ms"] == 260


def test_extract_timeline_returns_none_for_missing_audio() -> None:
    metrics = extract_timeline(
        [{"kind": "sent", "at_ms": 0}, {"kind": "completed", "at_ms": 30}]
    )

    assert metrics["t_first_audio_ms"] is None
    assert metrics["audio_chunk_count"] == 0
    assert metrics["audio_gap_max_ms"] is None
