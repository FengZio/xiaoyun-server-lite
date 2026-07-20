from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from config_matrix import build_group_snapshot, load_group_matrix, snapshot_fingerprint
from datasets import load_cases
from manager_api import ManagerApi, flatten_overrides
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


def run_group(group_id: str, runtime: dict[str, Any], matrix_path: Path, cases_path: Path, raw_root: Path, run_id: str, manager_api: Any = None) -> Path:
    matrix = load_group_matrix(matrix_path)
    snapshot = build_group_snapshot(matrix, group_id)
    run_dir = create_run_directory(raw_root, group_id, run_id)
    config_sha256 = snapshot_fingerprint(snapshot)
    (run_dir / "config_snapshot.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    baseline = None
    if manager_api is not None:
        baseline = manager_api.read_params()
        manager_api.apply_overrides(baseline, flatten_overrides(matrix["groups"][group_id].get("overrides", {})))
    try:
        for case in load_cases(cases_path):
            probe = run_ws_case(case, runtime["ws_url"], float(runtime.get("request_timeout_s", 45)))
            result = asyncio.run(probe) if asyncio.iscoroutine(probe) else probe
            append_jsonl(run_dir / "ws_records.jsonl", {"run_id": run_id, "group_id": group_id, "config_sha256": config_sha256, "case_id": case["case_id"], **result})
    finally:
        if manager_api is not None and baseline is not None:
            manager_api.restore(baseline)
    return run_dir


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["preflight", "run"])
    parser.add_argument("--runtime", required=True, type=Path)
    parser.add_argument("--group")
    args = parser.parse_args()
    runtime = yaml.safe_load(args.runtime.read_text(encoding="utf-8")) or {}
    errors = preflight(runtime)
    if errors:
        print("missing: " + ", ".join(errors))
        return 1
    if args.command == "run":
        if not args.group:
            parser.error("--group is required for run")
        root = Path(__file__).resolve().parents[1]
        token = os.getenv("MANAGER_API_TOKEN")
        manager_url = runtime.get("manager_api_url")
        manager_api = ManagerApi(manager_url, token) if token and manager_url else None
        run_dir = run_group(
            args.group,
            runtime,
            root / "configs" / "groups.yaml",
            root / "datasets" / "dialogue_cases.jsonl",
            root / "raw",
            datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
            manager_api=manager_api,
        )
        print(run_dir)
        return 0
    print("ok " + datetime.now(timezone.utc).isoformat())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
