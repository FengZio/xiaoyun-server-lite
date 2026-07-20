from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from config_matrix import build_group_snapshot, load_group_matrix, snapshot_fingerprint
from datasets import load_cases
from records import append_jsonl
from ws_probe import run_ws_case


def preflight(runtime: dict[str, Any]) -> list[str]:
    errors = []
    if not runtime.get("ws_url"):
        errors.append("WS_URL")
    if not runtime.get("service_b_url"):
        errors.append("SERVICE_B_URL")
    return errors


def create_run_directory(raw_root: Path, group_id: str, run_id: str) -> Path:
    path = raw_root / f"{run_id}_{group_id}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def run_group(group_id: str, runtime: dict[str, Any], matrix_path: Path, cases_path: Path, raw_root: Path, run_id: str) -> Path:
    snapshot = build_group_snapshot(load_group_matrix(matrix_path), group_id)
    run_dir = create_run_directory(raw_root, group_id, run_id)
    config_sha256 = snapshot_fingerprint(snapshot)
    (run_dir / "config_snapshot.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    for case in load_cases(cases_path):
        probe = run_ws_case(case, runtime["ws_url"], float(runtime.get("request_timeout_s", 45)))
        result = asyncio.run(probe) if asyncio.iscoroutine(probe) else probe
        append_jsonl(run_dir / "ws_records.jsonl", {"run_id": run_id, "group_id": group_id, "config_sha256": config_sha256, "case_id": case["case_id"], **result})
    return run_dir


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["preflight"])
    parser.add_argument("--runtime", required=True, type=Path)
    args = parser.parse_args()
    runtime = yaml.safe_load(args.runtime.read_text(encoding="utf-8")) or {}
    errors = preflight(runtime)
    if errors:
        print("missing: " + ", ".join(errors))
        return 1
    print("ok " + datetime.now(timezone.utc).isoformat())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
