from __future__ import annotations

import sys
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from datasets import load_cases


def test_load_cases_reads_jsonl_and_requires_case_id(tmp_path: Path) -> None:
    path = tmp_path / "cases.jsonl"
    path.write_text('{"case_id":"d1","input_text":"hello"}\n', encoding="utf-8")

    cases = load_cases(path)

    assert cases == [{"case_id": "d1", "input_text": "hello"}]
