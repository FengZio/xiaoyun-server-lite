from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_cases(path: Path) -> list[dict[str, Any]]:
    cases = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        case = json.loads(line)
        if not case.get("case_id"):
            raise ValueError(f"missing case_id on line {line_number}")
        if not case.get("input_text"):
            raise ValueError(f"missing input_text on line {line_number}")
        cases.append(case)
    return cases
