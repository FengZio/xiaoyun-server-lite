from __future__ import annotations

import time
from typing import Any

import httpx


def fetch_analysis(base_url: str, music_id: str) -> dict[str, Any]:
    response = httpx.get(f"{base_url.rstrip('/')}/api/music/analysis/{music_id}", timeout=5)
    response.raise_for_status()
    payload = response.json()
    return payload.get("data") or payload


def poll_analysis(base_url: str, music_id: str, deadline_s: float) -> dict[str, Any]:
    started = time.monotonic()
    while time.monotonic() - started < deadline_s:
        payload = fetch_analysis(base_url, music_id)
        if str(payload.get("state", "")).lower() in {"completed", "success", "done"}:
            return {
                "status": "ok",
                "analysis_latency_ms": round((time.monotonic() - started) * 1000, 3),
                "payload": payload,
            }
        time.sleep(0.2)
    return {"status": "timeout", "analysis_latency_ms": None}
