from __future__ import annotations

import random
import json
import os
from typing import Any

from openai import OpenAI


DIMENSIONS = (
    "empathy",
    "relevance",
    "naturalness",
    "continuity",
    "non_preachiness",
    "safety_boundary",
    "personalization",
)


def _prompt(reply_a: str, reply_b: str) -> str:
    dimensions = ", ".join(DIMENSIONS)
    return (
        "You are an independent dialogue evaluator. Score both responses independently from 1 to 5 for: "
        f"{dimensions}. Return valid JSON only with scores_a, scores_b, winner (A/B/tie), reason, and invalid_case. "
        "scores_a and scores_b must each contain exactly every listed dimension as a number from 1 to 5.\n"
        f"Response A:\n{reply_a}\n\nResponse B:\n{reply_b}"
    )


def build_judge_request(case: dict[str, Any], reply_a: str, reply_b: str, order_seed: int) -> dict[str, Any]:
    replies = [("A", reply_a), ("B", reply_b)]
    random.Random(order_seed).shuffle(replies)
    labels = {"first": replies[0][0], "second": replies[1][0]}
    prompt = _prompt(replies[0][1], replies[1][1])
    reverse_prompt = _prompt(replies[1][1], replies[0][1])
    return {"case_id": case["case_id"], "labels": labels, "prompt": prompt, "reverse_prompt": reverse_prompt}


def validate_judgment(payload: dict[str, Any]) -> dict[str, Any] | None:
    for score_key in ("scores_a", "scores_b"):
        scores = payload.get(score_key)
        if not isinstance(scores, dict) or set(scores) != set(DIMENSIONS):
            return None
        if any(not isinstance(value, (int, float)) or not 1 <= value <= 5 for value in scores.values()):
            return None
    if payload.get("winner") not in {"A", "B", "tie"}:
        return None
    if not isinstance(payload.get("reason"), str) or not isinstance(payload.get("invalid_case"), bool):
        return None
    return payload


def judge_pair(case: dict[str, Any], reply_a: str, reply_b: str, invoke: Any) -> list[dict[str, Any]]:
    request = build_judge_request(case, reply_a, reply_b, order_seed=7)
    prompts = [request["prompt"], request["reverse_prompt"], build_judge_request(case, reply_a, reply_b, order_seed=19)["prompt"]]
    results = []
    for index, prompt in enumerate(prompts, start=1):
        payload = validate_judgment(invoke(prompt))
        results.append({"case_id": case["case_id"], "pass_index": index, "valid": payload is not None, "judgment": payload})
    return results


def build_openai_client(base_url: str | None = None, api_key: str | None = None) -> OpenAI:
    options: dict[str, Any] = {"api_key": api_key or os.environ["OPENAI_API_KEY"], "timeout": 60}
    if base_url:
        options["base_url"] = base_url.rstrip("/")
    return OpenAI(**options)


def invoke_openai(prompt: str, base_url: str | None = None, api_key: str | None = None, model: str | None = None) -> dict[str, Any]:
    client = build_openai_client(base_url, api_key)
    response = client.chat.completions.create(
        model=model or os.environ.get("GPT_JUDGE_MODEL", "gpt-4o-mini"),
        temperature=0,
        response_format={"type": "json_object"},
        messages=[{"role": "user", "content": prompt}],
    )
    return json.loads(response.choices[0].message.content or "{}")
