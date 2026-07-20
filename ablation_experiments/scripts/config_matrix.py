from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import yaml


def load_group_matrix(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        matrix = yaml.safe_load(handle) or {}
    if not isinstance(matrix.get("baseline"), dict):
        raise ValueError("groups.yaml must define a baseline mapping")
    if not isinstance(matrix.get("groups"), dict):
        raise ValueError("groups.yaml must define a groups mapping")
    return matrix


def _merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def build_group_snapshot(matrix: dict[str, Any], group_id: str) -> dict[str, Any]:
    try:
        group = matrix["groups"][group_id]
    except KeyError as exc:
        raise KeyError(f"unknown experiment group: {group_id}") from exc
    overrides = group.get("overrides", {})
    if not isinstance(overrides, dict):
        raise ValueError(f"group {group_id} overrides must be a mapping")
    return _merge(matrix["baseline"], overrides)


def snapshot_fingerprint(snapshot: dict[str, Any]) -> str:
    canonical = json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
