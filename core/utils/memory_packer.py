import json
import re
from typing import Any, Dict, List, Tuple


def _tokenize(text: str) -> List[str]:
    return re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,}", (text or "").lower())


def _extract_memory_candidates(memory_str: str) -> List[str]:
    if not memory_str:
        return []
    text = memory_str.strip()
    if not text:
        return []

    def _flatten_json(obj: Any, prefix: str = "") -> List[str]:
        lines: List[str] = []
        if isinstance(obj, dict):
            for k, v in obj.items():
                key = f"{prefix}.{k}" if prefix else str(k)
                lines.extend(_flatten_json(v, key))
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                key = f"{prefix}[{i}]"
                lines.extend(_flatten_json(item, key))
        else:
            value = str(obj).strip()
            if value:
                lines.append(f"{prefix}: {value}" if prefix else value)
        return lines

    try:
        parsed = json.loads(text)
        lines = _flatten_json(parsed)
    except Exception:
        lines = [line.strip("- ").strip() for line in text.splitlines() if line.strip()]

    normalized = []
    for line in lines:
        normalized.append((line[:220] + "...") if len(line) > 220 else line)
    return normalized


def extract_long_term_items(memory_str: str, query: str, limit: int = 3) -> List[Dict[str, str]]:
    candidates = _extract_memory_candidates(memory_str)
    if not candidates:
        return []
    terms = _tokenize(query)
    scored: List[Tuple[int, int, str]] = []
    for idx, item in enumerate(candidates):
        score = 0
        item_lower = item.lower()
        for term in terms:
            if term in item_lower:
                score += 3
        scored.append((score, -idx, item))
    scored.sort(reverse=True)
    top = [x[2] for x in scored[: max(1, limit)]]
    return [{"source": "long_term", "type": "summary", "text": item} for item in top]


def pack(
    items: List[Dict[str, str]],
    current_user_text: str | None = None,
    max_items: int = 3,
) -> Tuple[str, Dict[str, int]]:
    stats = {
        "items_in": len(items or []),
        "items_out": 0,
        "dedup_hard": 0,
        "dedup_nearby": 0,
        "dropped_current": 0,
    }
    if not items:
        return "- 无", stats

    # hard dedup by exact text
    seen = set()
    filtered: List[Dict[str, str]] = []
    for item in items:
        text = (item.get("text") or "").strip()
        if not text:
            continue
        if current_user_text and text == current_user_text.strip():
            stats["dropped_current"] += 1
            continue
        if text in seen:
            stats["dedup_hard"] += 1
            continue
        seen.add(text)
        filtered.append(item)

    # nearby dedup within same source
    packed: List[Dict[str, str]] = []
    prev_by_source: Dict[str, Dict[str, str]] = {}
    for item in filtered:
        source = item.get("source", "unknown")
        text = item.get("text", "")
        prev = prev_by_source.get(source)
        if prev:
            prev_tokens = set(_tokenize(prev.get("text", "")))
            cur_tokens = set(_tokenize(text))
            overlap = 0.0
            if prev_tokens and cur_tokens:
                overlap = len(prev_tokens & cur_tokens) / max(
                    1, max(len(prev_tokens), len(cur_tokens))
                )
            if overlap >= 0.8:
                stats["dedup_nearby"] += 1
                if len(text) > len(prev.get("text", "")):
                    prev_by_source[source] = item
                    packed[-1] = item
                continue
        prev_by_source[source] = item
        packed.append(item)

    packed = packed[: max(1, max_items)]
    stats["items_out"] = len(packed)

    lines = []
    for item in packed:
        source = item.get("source", "unknown")
        text = item.get("text", "")
        lines.append(f"- ({source}) {text}")
    return "\n".join(lines) if lines else "- 无", stats
