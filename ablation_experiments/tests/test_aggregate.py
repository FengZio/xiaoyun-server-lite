from __future__ import annotations

import sys
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from aggregate import aggregate_dialogue, aggregate_services, aggregate_voice


def test_aggregate_voice_reports_first_audio_and_completion() -> None:
    result = aggregate_voice(
        [{"status": "ok", "t_first_audio_ms": 200, "t_complete_ms": 700}]
    )

    assert result["t_first_audio_p50_ms"] == 200
    assert result["t_complete_p50_ms"] == 700


def test_aggregate_dialogue_reports_completion_percentiles() -> None:
    result = aggregate_dialogue([{"status": "ok", "t_complete_ms": 100}])

    assert result["t_complete_p50_ms"] == 100
    assert result["success_rate"] == 1.0


def test_aggregate_services_reports_boolean_rates() -> None:
    result = aggregate_services([
        {"status": "ok", "task_enqueued": True, "task_success": True, "persona_hit": True},
        {"status": "timeout", "task_enqueued": True, "task_success": False, "persona_hit": False},
    ])

    assert result["task_enqueue_rate"] == 1.0
    assert result["task_success_rate"] == 0.5
    assert result["persona_hit_rate"] == 0.5
