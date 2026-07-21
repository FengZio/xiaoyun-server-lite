from __future__ import annotations

import json
from pathlib import Path


RENAMES = {
    "t_first_token_ms": "t_first_visible_reply_ms",
    "t_complete_ms": "t_reply_complete_ms",
}


def migrate_file(path: Path) -> int:
    records = []
    changed = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        for old_key, new_key in RENAMES.items():
            if old_key in record:
                record[new_key] = record.pop(old_key)
                changed += 1
        records.append(record)
    path.write_text("".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records), encoding="utf-8")
    return changed


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1] / "raw"
    total = sum(migrate_file(path) for path in root.rglob("ws_records.jsonl"))
    print(total)
