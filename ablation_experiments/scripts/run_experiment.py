from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


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
