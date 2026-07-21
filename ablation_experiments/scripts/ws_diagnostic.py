from __future__ import annotations

import asyncio
import json
import sys

import websockets


async def diagnose(endpoint: str) -> None:
    print("connecting", flush=True)
    async with websockets.connect(
        endpoint,
        additional_headers={"device-id": "ablation_diagnostic", "client-id": "ablation"},
    ) as websocket:
        print("connected", flush=True)
        await websocket.send(json.dumps({"type": "hello", "features": {"mcp": False}}))
        print("hello_sent", flush=True)
        try:
            print(await asyncio.wait_for(websocket.recv(), timeout=8), flush=True)
            await websocket.send(json.dumps({"type": "chat", "content": "今天有点累。", "enable_audio": False, "agent_id": "db2bc87d21764c128b96711f948aa049"}))
            while True:
                print(await asyncio.wait_for(websocket.recv(), timeout=45), flush=True)
        except Exception as error:
            print(f"receive_error={error!r}", flush=True)


if __name__ == "__main__":
    asyncio.run(diagnose(sys.argv[1]))
