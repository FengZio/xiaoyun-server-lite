from __future__ import annotations

import sys
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from gpt_judge import build_judge_request, validate_judgment


def test_judge_request_hides_group_ids_and_reverses_order() -> None:
    request = build_judge_request({"case_id": "c1"}, "reply alpha", "reply beta", order_seed=7)

    assert "D0" not in request["prompt"]
    assert set(request["labels"].values()) == {"A", "B"}
    assert request["reverse_prompt"] != request["prompt"]


def test_validate_judgment_rejects_out_of_range_scores() -> None:
    payload = {"scores": {"empathy": 6}, "winner": "A", "reason": "x", "invalid_case": False}

    assert validate_judgment(payload) is None
