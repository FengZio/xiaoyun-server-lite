from __future__ import annotations

import sys
from pathlib import Path

import yaml


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from config_matrix import build_group_snapshot, load_group_matrix, snapshot_fingerprint


def write_matrix(tmp_path: Path) -> Path:
    path = tmp_path / "groups.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "baseline": {
                    "dialogue_policy": {"enabled": True},
                    "integration": {"enable_persona": True},
                    "sentence_pipeline": {"enabled": True},
                },
                "groups": {
                    "D1": {"overrides": {"dialogue_policy": {"enabled": False}}}
                },
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


def test_d1_only_disables_dialogue_policy(tmp_path: Path) -> None:
    matrix = load_group_matrix(write_matrix(tmp_path))

    snapshot = build_group_snapshot(matrix, "D1")

    assert snapshot["dialogue_policy"]["enabled"] is False
    assert snapshot["integration"]["enable_persona"] is True
    assert snapshot["sentence_pipeline"]["enabled"] is True


def test_building_one_group_does_not_mutate_baseline(tmp_path: Path) -> None:
    matrix = load_group_matrix(write_matrix(tmp_path))

    build_group_snapshot(matrix, "D1")

    assert matrix["baseline"]["dialogue_policy"]["enabled"] is True


def test_snapshot_fingerprint_is_stable_for_identical_content(tmp_path: Path) -> None:
    matrix = load_group_matrix(write_matrix(tmp_path))
    snapshot = build_group_snapshot(matrix, "D1")

    assert snapshot_fingerprint(snapshot) == snapshot_fingerprint(dict(snapshot))
