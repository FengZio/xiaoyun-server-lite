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


def aggregate_dialogue(records: list[dict[str, Any]]) -> dict[str, Any]:
    completion = summarize_latencies(records, "t_complete_ms")
    return {
        "request_count": completion["request_count"],
        "success_rate": completion["success_rate"],
        "t_complete_p50_ms": completion["p50_ms"],
        "t_complete_p95_ms": completion["p95_ms"],
        "t_complete_p99_ms": completion["p99_ms"],
    }


def aggregate_services(records: list[dict[str, Any]]) -> dict[str, Any]:
    count = len(records)
    rate = lambda field: sum(bool(record.get(field)) for record in records) / count if count else 0.0
    return {
        "request_count": count,
        "task_enqueue_rate": rate("task_enqueued"),
        "task_success_rate": rate("task_success"),
        "persona_hit_rate": rate("persona_hit"),
    }
