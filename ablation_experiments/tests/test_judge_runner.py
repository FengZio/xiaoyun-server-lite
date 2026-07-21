from __future__ import annotations

import json
import sys
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from judge_runner import build_pairs, completed_case_ids


def test_build_pairs_matches_same_case_id(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.jsonl"
    variant = tmp_path / "variant.jsonl"
    baseline.write_text(json.dumps({"case_id": "c1", "status": "ok", "reply": "base"}) + "\n", encoding="utf-8")
    variant.write_text(json.dumps({"case_id": "c1", "status": "ok", "reply": "variant"}) + "\n", encoding="utf-8")

    pairs = build_pairs(baseline, variant)

    assert pairs == [{"case_id": "c1", "reply_a": "base", "reply_b": "variant"}]


def test_completed_case_ids_requires_three_records(tmp_path: Path) -> None:
    path = tmp_path / "judgments.jsonl"
    path.write_text("\n".join(json.dumps({"case_id": "c1"}) for _ in range(3)) + "\n", encoding="utf-8")

    assert completed_case_ids(path) == {"c1"}
