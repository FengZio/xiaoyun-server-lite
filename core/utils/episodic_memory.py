import os
import re
import time
from typing import Any, Dict, List

import yaml

from config.config_loader import get_project_dir


FOLLOWUP_HINTS = ("明天", "下次", "回头", "之后", "晚点", "再聊", "跟进", "提醒我")
RESOLVED_HINTS = ("好了", "搞定", "解决了", "不用了", "没事了", "完成了")
EMOTION_HINTS = ("难受", "焦虑", "开心", "生气", "疲惫", "难过", "烦")


def _tokenize(text: str) -> List[str]:
    return re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,}", (text or "").lower())


class EpisodicMemoryStore:
    """Lightweight episodic memory store with unfinished-topic tracking."""

    def __init__(self, path: str = None):
        self.path = path or os.path.join(get_project_dir(), "data", ".episodic_memory.yaml")
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._load_all()

    def _load_all(self) -> None:
        if not os.path.exists(self.path):
            self._cache = {}
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                self._cache = yaml.safe_load(f) or {}
        except Exception:
            self._cache = {}

    def _save_all(self) -> None:
        folder = os.path.dirname(self.path)
        if folder and not os.path.exists(folder):
            os.makedirs(folder, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            yaml.safe_dump(self._cache, f, allow_unicode=True, sort_keys=False)

    def _default_user(self) -> Dict[str, Any]:
        return {
            "events": [],
            "pending_topics": [],
            "max_events": 10,
        }

    def _get_user(self, user_id: str) -> Dict[str, Any]:
        if user_id not in self._cache:
            self._cache[user_id] = self._default_user()
        return self._cache[user_id]

    def observe_user_input(self, user_id: str, text: str) -> bool:
        """Update unresolved topic state before model response."""
        if not user_id or not text:
            return False
        profile = self._get_user(user_id)
        changed = False
        text_lower = text.lower()
        tokens = set(_tokenize(text))

        # Mark topics as resolved when user says issue is done.
        if any(k in text_lower for k in RESOLVED_HINTS):
            for topic in profile.get("pending_topics", []):
                if topic.get("resolved"):
                    continue
                topic_tokens = set(topic.get("keywords", []))
                if not topic_tokens or topic_tokens.intersection(tokens):
                    topic["resolved"] = True
                    topic["last_updated"] = int(time.time())
                    changed = True

        if changed:
            self._save_all()
        return changed

    def update_from_turn(
        self,
        user_id: str,
        user_text: str,
        assistant_text: str,
        context: Dict[str, Any] | None = None,
    ) -> bool:
        """Persist one event card and optionally track unfinished topics."""
        if not user_id or not user_text:
            return False
        profile = self._get_user(user_id)
        now = int(time.time())
        changed = False

        user_preview = user_text.strip()
        if len(user_preview) > 80:
            user_preview = user_preview[:80] + "..."
        assistant_preview = (assistant_text or "").strip()
        if len(assistant_preview) > 80:
            assistant_preview = assistant_preview[:80] + "..."

        emotion_hint = ""
        for k in EMOTION_HINTS:
            if k in user_text:
                emotion_hint = k
                break

        event = {
            "time": now,
            "user": user_preview,
            "assistant": assistant_preview,
            "emotion_hint": emotion_hint,
            "keywords": _tokenize(user_text)[:8],
        }
        if isinstance(context, dict):
            compact_context = {
                key: value
                for key, value in context.items()
                if value not in ("", None, False)
            }
            if compact_context:
                event["context"] = compact_context
        profile["events"].append(event)
        max_events = int(profile.get("max_events", 10))
        if len(profile["events"]) > max_events:
            profile["events"] = profile["events"][-max_events:]
        changed = True

        if any(k in user_text for k in FOLLOWUP_HINTS):
            topic = {
                "topic": user_preview,
                "keywords": _tokenize(user_text)[:8],
                "created_at": now,
                "last_updated": now,
                "resolved": False,
            }
            profile["pending_topics"].append(topic)
            profile["pending_topics"] = profile["pending_topics"][-5:]

        if changed:
            self._save_all()
        return changed

    def query_relevant(self, user_id: str, query: str, limit: int = 3) -> List[str]:
        profile = self._get_user(user_id)
        q_tokens = set(_tokenize(query))
        ranked: List[tuple[int, str]] = []

        for idx, topic in enumerate(reversed(profile.get("pending_topics", []))):
            if topic.get("resolved"):
                continue
            score = 6 - idx
            topic_tokens = set(topic.get("keywords", []))
            if topic_tokens and topic_tokens.intersection(q_tokens):
                score += 4
            ranked.append((score, f"未完话题: {topic.get('topic', '')}"))

        for idx, event in enumerate(reversed(profile.get("events", []))):
            score = 3 - min(idx, 2)
            event_tokens = set(event.get("keywords", []))
            if event_tokens and event_tokens.intersection(q_tokens):
                score += 3
            text = event.get("user", "")
            ranked.append((score, f"近期事件: {text}"))

        ranked.sort(key=lambda x: x[0], reverse=True)
        result: List[str] = []
        seen = set()
        for _, item in ranked:
            if item and item not in seen:
                seen.add(item)
                result.append(item)
            if len(result) >= limit:
                break
        return result

    def query_relevant_struct(
        self, user_id: str, query: str, limit: int = 3
    ) -> List[Dict[str, Any]]:
        profile = self._get_user(user_id)
        q_tokens = set(_tokenize(query))
        ranked: List[tuple[int, Dict[str, Any]]] = []

        for idx, topic in enumerate(reversed(profile.get("pending_topics", []))):
            if topic.get("resolved"):
                continue
            score = 6 - idx
            topic_tokens = set(topic.get("keywords", []))
            if topic_tokens and topic_tokens.intersection(q_tokens):
                score += 4
            ranked.append(
                (
                    score,
                    {
                        "source": "episodic",
                        "type": "unfinished",
                        "time": topic.get("last_updated") or topic.get("created_at"),
                        "text": f"未完话题: {topic.get('topic', '')}",
                    },
                )
            )

        for idx, event in enumerate(reversed(profile.get("events", []))):
            score = 3 - min(idx, 2)
            event_tokens = set(event.get("keywords", []))
            if event_tokens and event_tokens.intersection(q_tokens):
                score += 3
            ranked.append(
                (
                    score,
                    {
                        "source": "episodic",
                        "type": "recent",
                        "time": event.get("time"),
                        "text": f"近期事件: {event.get('user', '')}",
                    },
                )
            )

        ranked.sort(key=lambda x: x[0], reverse=True)
        result: List[Dict[str, Any]] = []
        seen = set()
        for _, item in ranked:
            text = item.get("text") or ""
            if text and text not in seen:
                seen.add(text)
                result.append(item)
            if len(result) >= limit:
                break
        return result
