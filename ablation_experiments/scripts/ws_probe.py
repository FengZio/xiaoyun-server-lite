from __future__ import annotations

from typing import Any


def extract_timeline(events: list[dict[str, Any]]) -> dict[str, int | None]:
    first = {"text": None, "audio": None, "completed": None}
    sent_at = next((event["at_ms"] for event in events if event.get("kind") == "sent"), 0)
    for event in events:
        kind = event.get("kind")
        if kind in first and first[kind] is None:
            first[kind] = event.get("at_ms")
    return {
        "t_first_token_ms": _elapsed(first["text"], sent_at),
        "t_first_audio_ms": _elapsed(first["audio"], sent_at),
        "t_complete_ms": _elapsed(first["completed"], sent_at),
    }


def _elapsed(timestamp: int | None, sent_at: int) -> int | None:
    return None if timestamp is None else int(timestamp - sent_at)
