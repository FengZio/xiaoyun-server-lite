from __future__ import annotations

import sys
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from gpt_judge import build_judge_request, judge_pair, validate_judgment


def test_judge_request_hides_group_ids_and_reverses_order() -> None:
    request = build_judge_request({"case_id": "c1"}, "reply alpha", "reply beta", order_seed=7)

    assert "D0" not in request["prompt"]
    assert set(request["labels"].values()) == {"A", "B"}
    assert request["reverse_prompt"] != request["prompt"]


def test_validate_judgment_rejects_out_of_range_scores() -> None:
    payload = {"scores": {"empathy": 6}, "winner": "A", "reason": "x", "invalid_case": False}

    assert validate_judgment(payload) is None


def test_judge_pair_runs_three_anonymous_orders() -> None:
    payload = {
        "scores": {
            "empathy": 4,
            "relevance": 4,
            "naturalness": 4,
            "continuity": 4,
            "non_preachiness": 4,
            "safety_boundary": 5,
            "personalization": 3,
        },
        "winner": "A",
        "reason": "consistent",
        "invalid_case": False,
    }

    results = judge_pair({"case_id": "c1"}, "left", "right", lambda _: payload)

    assert len(results) == 3
    assert {result["pass_index"] for result in results} == {1, 2, 3}


def test_openai_client_uses_configured_base_url(monkeypatch) -> None:
    captured = {}

    class Client:
        def __init__(self, **kwargs): captured.update(kwargs)

    monkeypatch.setattr("gpt_judge.OpenAI", Client)
    monkeypatch.setenv("OPENAI_API_KEY", "key")

    from gpt_judge import build_openai_client
    build_openai_client("https://gateway.example/v1")

    assert captured["base_url"] == "https://gateway.example/v1"
    assert captured["timeout"] == 60


def test_openai_client_uses_explicit_runtime_key(monkeypatch) -> None:
    captured = {}

    class Client:
        def __init__(self, **kwargs): captured.update(kwargs)

    monkeypatch.setattr("gpt_judge.OpenAI", Client)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    from gpt_judge import build_openai_client
    build_openai_client(api_key="runtime-test-key")

    assert captured["api_key"] == "runtime-test-key"
