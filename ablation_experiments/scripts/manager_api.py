from __future__ import annotations

from typing import Any

import httpx


def flatten_overrides(values: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    flattened: dict[str, Any] = {}
    for key, value in values.items():
        code = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            flattened.update(flatten_overrides(value, code))
        else:
            flattened[code] = value
    return flattened


class ManagerApi:
    def __init__(self, base_url: str, token: str, transport: httpx.BaseTransport | None = None):
        self.client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            transport=transport,
            timeout=15,
        )

    def read_params(self) -> dict[str, dict[str, Any]]:
        response = self.client.get("/admin/params/page", params={"page": 1, "limit": 1000})
        response.raise_for_status()
        data = response.json().get("data") or {}
        return {item["paramCode"]: item for item in data.get("list", [])}

    def apply_overrides(self, baseline: dict[str, dict[str, Any]], overrides: dict[str, Any]) -> None:
        for code, value in overrides.items():
            original = baseline[code]
            payload = {**original, "paramValue": str(value).lower() if isinstance(value, bool) else str(value)}
            response = self.client.put("/admin/params", json=payload)
            response.raise_for_status()

    def restore(self, baseline: dict[str, dict[str, Any]]) -> None:
        for payload in baseline.values():
            response = self.client.put("/admin/params", json=payload)
            response.raise_for_status()
