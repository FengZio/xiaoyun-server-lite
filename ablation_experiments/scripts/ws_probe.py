from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import websockets


def extract_timeline(events: list[dict[str, Any]]) -> dict[str, int | None]:
    first = {"text": None, "audio": None, "completed": None}
    sent_at = next((event["at_ms"] for event in events if event.get("kind") == "sent"), 0)
    for event in events:
        kind = event.get("kind")
        if kind in first and first[kind] is None:
            first[kind] = event.get("at_ms")
    return {
        "t_first_visible_reply_ms": _elapsed(first["text"], sent_at),
        "t_first_audio_ms": _elapsed(first["audio"], sent_at),
        "t_reply_complete_ms": _elapsed(first["completed"], sent_at),
    }


def _elapsed(timestamp: int | None, sent_at: int) -> int | None:
    return None if timestamp is None else int(timestamp - sent_at)


async def run_ws_case(case: dict[str, Any], endpoint: str, timeout_s: float) -> dict[str, Any]:
    device_id = f"ablation_{case['case_id']}_{int(time.time() * 1000)}"
    events: list[dict[str, Any]] = [{"kind": "sent", "at_ms": 0}]
    started = time.perf_counter()
    async with websockets.connect(endpoint, additional_headers={"device-id": device_id, "client-id": "ablation"}) as websocket:
        await websocket.send(json.dumps({"type": "hello", "features": {"mcp": False}}))
        hello = json.loads(await asyncio.wait_for(websocket.recv(), timeout=timeout_s))
        if hello.get("type") != "hello":
            raise RuntimeError(f"unexpected handshake message: {hello.get('type')}")
        message = {"type": "chat", "content": case["input_text"], "enable_audio": case.get("enable_audio", False)}
        if case.get("agent_id"):
            message["agent_id"] = case["agent_id"]
        await websocket.send(json.dumps(message))
        while True:
            raw = await asyncio.wait_for(websocket.recv(), timeout=timeout_s)
            message = json.loads(raw)
            at_ms = round((time.perf_counter() - started) * 1000)
            message_type = message.get("type")
            if message_type in {"reply_start", "reply_text_chunk"} or (message_type == "reply" and message.get("state") == "start"):
                events.append({"kind": "text", "at_ms": at_ms})
            if message.get("audio_url") or message_type == "reply_audio_chunk":
                events.append({"kind": "audio", "at_ms": at_ms})
            is_complete = message_type == "reply_end" or (message_type == "reply" and message.get("state") in {"end", "complete", "error"})
            if is_complete:
                events.append({"kind": "completed", "at_ms": at_ms})
                status = "error" if message.get("state") == "error" else "ok"
                return {"status": status, "events": events, **extract_timeline(events), "reply": message.get("text", "")}
