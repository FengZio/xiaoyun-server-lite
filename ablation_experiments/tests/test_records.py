from __future__ import annotations

import sys
from pathlib import Path

import pytest


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from records import append_jsonl, summarize_latencies


def test_summary_reports_success_rate_and_p95() -> None:
    records = [
        {"status": "ok", "latency_ms": 10},
        {"status": "timeout", "latency_ms": None},
        {"status": "ok", "latency_ms": 30},
    ]

    summary = summarize_latencies(records, "latency_ms")

    assert summary["request_count"] == 3
    assert summary["success_rate"] == pytest.approx(2 / 3)
    assert summary["p95_ms"] >= 10


def test_append_jsonl_preserves_existing_records(tmp_path: Path) -> None:
    path = tmp_path / "records.jsonl"

    append_jsonl(path, {"case_id": "one"})
    append_jsonl(path, {"case_id": "two"})

    assert [line for line in path.read_text(encoding="utf-8").splitlines()] == [
        '{"case_id":"one"}',
        '{"case_id":"two"}',
    ]
