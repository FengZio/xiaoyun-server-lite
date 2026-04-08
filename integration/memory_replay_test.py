import argparse
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List

import yaml


PROJECT_DIR = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(PROJECT_DIR))

from core.utils.dialogue import Message  # noqa: E402
from core.utils.dialogue_policy import build_dialogue_policy  # noqa: E402
from core.utils.episodic_memory import EpisodicMemoryStore  # noqa: E402
from core.utils.stable_memory import StableMemoryStore  # noqa: E402


def _load_yaml(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _load_cases(path: Path) -> List[Dict[str, Any]]:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    if path.suffix.lower() == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    data = json.loads(text)
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("cases"), list):
        return data["cases"]
    raise ValueError("输入文件格式无效：应为 JSON 列表，或 {'cases': [...]}，或 JSONL。")


def _normalize_turn(turn: Any) -> Dict[str, Any]:
    if isinstance(turn, str):
        return {"user": turn}
    if isinstance(turn, dict):
        return turn
    raise ValueError(f"无效 turn: {turn}")


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _clear_user_memory(
    stable_store: StableMemoryStore, episodic_store: EpisodicMemoryStore, user_id: str
) -> None:
    if user_id in stable_store._cache:
        stable_store._cache.pop(user_id, None)
        stable_store._save_all()
    if user_id in episodic_store._cache:
        episodic_store._cache.pop(user_id, None)
        episodic_store._save_all()


def _first_memory_summary(policy: Dict[str, Any]) -> str:
    items = policy.get("memory_to_inject", [])
    if not items:
        return ""
    first = items[0]
    if isinstance(first, dict):
        return first.get("summary", "")
    return str(first)


def _first_memory_source(policy: Dict[str, Any]) -> str:
    items = policy.get("memory_to_inject", [])
    if not items:
        return ""
    first = items[0]
    if isinstance(first, dict):
        return first.get("source", "")
    return ""


def _check_expect(policy: Dict[str, Any], expect: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    items = policy.get("memory_to_inject", [])
    summaries = []
    for item in items:
        if isinstance(item, dict):
            summaries.append(item.get("summary", ""))
        else:
            summaries.append(str(item))

    for token in expect.get("must_include", []):
        if not any(token in s for s in summaries):
            errors.append(f"缺少预期记忆片段: {token}")

    first_source = expect.get("first_source")
    if first_source and _first_memory_source(policy) != first_source:
        errors.append(f"首条 source 不匹配: 期望 {first_source}, 实际 {_first_memory_source(policy)}")

    first_prefix = expect.get("first_prefix")
    if first_prefix and not _first_memory_summary(policy).startswith(first_prefix):
        errors.append(
            f"首条 summary 前缀不匹配: 期望 {first_prefix}, 实际 {_first_memory_summary(policy)}"
        )
    return errors


def run_case(
    case: Dict[str, Any],
    config: Dict[str, Any],
    stable_store: StableMemoryStore,
    episodic_store: EpisodicMemoryStore,
    reset_user_memory: bool,
) -> Dict[str, Any]:
    case_id = case.get("case_id", f"case_{int(time.time() * 1000)}")
    user_id = case.get("user_id", f"replay_user_{int(time.time() * 1000)}")
    turns = [_normalize_turn(t) for t in case.get("turns", [])]
    if reset_user_memory:
        _clear_user_memory(stable_store, episodic_store, user_id)

    dialogue: List[Message] = []
    last_intent = None
    turn_reports: List[Dict[str, Any]] = []
    unfinished_first_checks = 0
    unfinished_first_hits = 0

    for idx, turn in enumerate(turns, start=1):
        user_text = (turn.get("user") or "").strip()
        if not user_text:
            continue
        assistant_text = (turn.get("assistant") or "收到，我先陪你把这个事情理顺。").strip()

        stable_store.update_from_user_text(user_id, user_text)
        episodic_store.observe_user_input(user_id, user_text)

        stable_items = stable_store.query_relevant(user_id, user_text, limit=2)
        episodic_items = episodic_store.query_relevant(user_id, user_text, limit=3)
        policy = build_dialogue_policy(
            query=user_text,
            dialogue=dialogue,
            memory_str="",
            config=config,
            stable_memory_items=stable_items,
            episodic_memory_items=episodic_items,
            last_intent=last_intent,
            music_signals=turn.get("music_signals") or case.get("music_signals"),
        )
        last_intent = policy.get("intent")

        has_unfinished = any(item.startswith("未完话题:") for item in episodic_items)
        unfinished_first_ok = (
            not has_unfinished
            or (
                _first_memory_source(policy) == "episodic"
                and _first_memory_summary(policy).startswith("未完话题:")
            )
        )
        if has_unfinished:
            unfinished_first_checks += 1
            if unfinished_first_ok:
                unfinished_first_hits += 1

        expect = turn.get("expect", {})
        expect_errors = _check_expect(policy, expect) if isinstance(expect, dict) else []

        turn_reports.append(
            {
                "turn": idx,
                "user": user_text,
                "intent": policy.get("intent"),
                "stable_items": stable_items,
                "episodic_items": episodic_items,
                "memory_to_inject": policy.get("memory_to_inject", []),
                "has_unfinished_topic": has_unfinished,
                "unfinished_first_ok": unfinished_first_ok,
                "expect_errors": expect_errors,
            }
        )

        episodic_store.update_from_turn(user_id, user_text, assistant_text)
        dialogue.append(Message(role="user", content=user_text))
        dialogue.append(Message(role="assistant", content=assistant_text))

    total_expect_errors = sum(len(x["expect_errors"]) for x in turn_reports)
    return {
        "case_id": case_id,
        "user_id": user_id,
        "turn_count": len(turn_reports),
        "unfinished_first_checks": unfinished_first_checks,
        "unfinished_first_hits": unfinished_first_hits,
        "unfinished_first_hit_rate": (
            round(unfinished_first_hits / unfinished_first_checks, 4)
            if unfinished_first_checks
            else 1.0
        ),
        "expect_errors": total_expect_errors,
        "passed": total_expect_errors == 0,
        "turn_reports": turn_reports,
    }


def print_human_report(report: Dict[str, Any]) -> None:
    print(
        f"[{report['case_id']}] user_id={report['user_id']} "
        f"turns={report['turn_count']} passed={report['passed']}"
    )
    print(
        "  unfinished_first:"
        f" {report['unfinished_first_hits']}/{report['unfinished_first_checks']}"
        f" hit_rate={report['unfinished_first_hit_rate']}"
    )
    if report["expect_errors"] > 0:
        print(f"  expect_errors: {report['expect_errors']}")
    for tr in report["turn_reports"]:
        first = tr["memory_to_inject"][0] if tr["memory_to_inject"] else {}
        first_summary = first.get("summary", "") if isinstance(first, dict) else str(first)
        print(
            f"  - turn#{tr['turn']} intent={tr['intent']} "
            f"first_memory={first_summary if first_summary else 'None'} "
            f"unfinished_first_ok={tr['unfinished_first_ok']}"
        )
        for err in tr["expect_errors"]:
            print(f"    ! {err}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Dialogue Policy 记忆回放测试")
    parser.add_argument(
        "--input",
        required=True,
        help="回放数据文件路径（JSON/JSONL）",
    )
    parser.add_argument(
        "--output",
        default="",
        help="测试报告输出路径（JSON，可选）",
    )
    parser.add_argument(
        "--config",
        default=str(PROJECT_DIR / "config.yaml"),
        help="配置文件路径（默认项目根 config.yaml）",
    )
    parser.add_argument(
        "--use-live-memory-store",
        action="store_true",
        help="使用线上默认 memory 文件；默认使用隔离 replay 文件，避免污染现有数据",
    )
    parser.add_argument(
        "--reset-user-memory",
        action="store_true",
        help="每个 case 开始前清空该 user_id 的 stable/episodic 记忆",
    )
    args = parser.parse_args()

    input_path = Path(args.input).resolve()
    config_path = Path(args.config).resolve()
    if not input_path.exists():
        raise FileNotFoundError(f"输入文件不存在: {input_path}")
    if not config_path.exists():
        raise FileNotFoundError(f"配置文件不存在: {config_path}")

    config = _load_yaml(config_path)
    cases = _load_cases(input_path)
    if not cases:
        raise ValueError("输入文件中没有可执行 case")

    if args.use_live_memory_store:
        stable_store = StableMemoryStore()
        episodic_store = EpisodicMemoryStore()
    else:
        stable_path = PROJECT_DIR / "data" / ".stable_memory.replay.yaml"
        episodic_path = PROJECT_DIR / "data" / ".episodic_memory.replay.yaml"
        _ensure_parent(stable_path)
        _ensure_parent(episodic_path)
        stable_store = StableMemoryStore(path=str(stable_path))
        episodic_store = EpisodicMemoryStore(path=str(episodic_path))

    all_reports = []
    for case in cases:
        report = run_case(
            case=case,
            config=config,
            stable_store=stable_store,
            episodic_store=episodic_store,
            reset_user_memory=args.reset_user_memory,
        )
        all_reports.append(report)
        print_human_report(report)

    summary = {
        "total_cases": len(all_reports),
        "passed_cases": sum(1 for r in all_reports if r["passed"]),
        "failed_cases": sum(1 for r in all_reports if not r["passed"]),
        "unfinished_first_checks": sum(r["unfinished_first_checks"] for r in all_reports),
        "unfinished_first_hits": sum(r["unfinished_first_hits"] for r in all_reports),
        "reports": all_reports,
    }
    checks = summary["unfinished_first_checks"]
    summary["unfinished_first_hit_rate"] = (
        round(summary["unfinished_first_hits"] / checks, 4) if checks else 1.0
    )

    print("\n=== SUMMARY ===")
    print(
        f"cases: {summary['passed_cases']}/{summary['total_cases']} passed, "
        f"unfinished_first_hit_rate={summary['unfinished_first_hit_rate']}"
    )

    if args.output:
        out = Path(args.output).resolve()
        _ensure_parent(out)
        out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"report saved: {out}")


if __name__ == "__main__":
    main()
