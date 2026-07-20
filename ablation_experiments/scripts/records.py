from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
        handle.write("\n")


def percentile(values: list[float], percentage: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil((percentage / 100) * len(ordered)) - 1)
    return ordered[index]


def summarize_latencies(records: list[dict[str, Any]], field: str) -> dict[str, Any]:
    successful = [record for record in records if record.get("status") == "ok"]
    values = [float(record[field]) for record in successful if record.get(field) is not None]
    request_count = len(records)
    return {
        "request_count": request_count,
        "success_count": len(successful),
        "success_rate": len(successful) / request_count if request_count else 0.0,
        "mean_ms": sum(values) / len(values) if values else None,
        "p50_ms": percentile(values, 50),
        "p95_ms": percentile(values, 95),
        "p99_ms": percentile(values, 99),
    }
