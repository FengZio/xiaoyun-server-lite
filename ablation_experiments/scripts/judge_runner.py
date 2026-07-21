from __future__ import annotations

import json
import argparse
from pathlib import Path
from typing import Any

import yaml

from gpt_judge import invoke_openai, judge_pair


def _load_replies(path: Path) -> dict[str, str]:
    replies: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if record.get("status") == "ok" and record.get("reply"):
            replies[record["case_id"]] = record["reply"]
    return replies


def build_pairs(baseline_path: Path, variant_path: Path) -> list[dict[str, Any]]:
    baseline = _load_replies(baseline_path)
    variant = _load_replies(variant_path)
    return [
        {"case_id": case_id, "reply_a": baseline[case_id], "reply_b": variant[case_id]}
        for case_id in sorted(baseline.keys() & variant.keys())
    ]


def run_judgments(baseline_path: Path, variant_path: Path, output_path: Path, runtime: dict[str, Any]) -> int:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    model = "gpt-5.6-terra"
    invoke = lambda prompt: invoke_openai(prompt, runtime.get("gpt_base_url"), runtime.get("gpt_api_key"), model)
    count = 0
    with output_path.open("a", encoding="utf-8", newline="\n") as handle:
        for pair in build_pairs(baseline_path, variant_path):
            for result in judge_pair({"case_id": pair["case_id"]}, pair["reply_a"], pair["reply_b"], invoke):
                handle.write(json.dumps({"baseline": "D0", "model": model, **result}, ensure_ascii=False) + "\n")
                count += 1
    return count


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--variant", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--runtime", required=True, type=Path)
    args = parser.parse_args()
    runtime = yaml.safe_load(args.runtime.read_text(encoding="utf-8")) or {}
    print(run_judgments(args.baseline, args.variant, args.output, runtime))
