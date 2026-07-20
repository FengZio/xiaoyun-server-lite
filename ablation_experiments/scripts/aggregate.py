from __future__ import annotations

from typing import Any

from records import summarize_latencies


def aggregate_voice(records: list[dict[str, Any]]) -> dict[str, Any]:
    first_audio = summarize_latencies(records, "t_first_audio_ms")
    complete = summarize_latencies(records, "t_complete_ms")
    return {
        "request_count": first_audio["request_count"],
        "success_rate": first_audio["success_rate"],
        "t_first_audio_p50_ms": first_audio["p50_ms"],
        "t_first_audio_p95_ms": first_audio["p95_ms"],
        "t_complete_p50_ms": complete["p50_ms"],
        "t_complete_p95_ms": complete["p95_ms"],
    }
