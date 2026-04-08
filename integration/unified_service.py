"""Unified service client for persona and emotion statistics."""

from typing import Any, Dict, Optional
import logging

import requests

logger = logging.getLogger(__name__)


def _pick_float(data: Dict[str, Any], keys: list[str]) -> Optional[float]:
    for key in keys:
        value = data.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _pick_str(data: Dict[str, Any], keys: list[str]) -> str:
    for key in keys:
        value = data.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _fmt_float(value: Optional[float]) -> str:
    if value is None:
        return ""
    return f"{value:.2f}"


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _infer_mood_label(stats: Dict[str, Any]) -> str:
    explicit = _pick_str(
        stats,
        [
            "current_mood_label",
            "mood_label",
            "emotion_label",
            "recent_emotion_label",
            "current_emotion_label",
        ],
    )
    if explicit:
        return explicit

    valence = _pick_float(stats, ["current_valence", "valence", "emotion_valence"])
    arousal = _pick_float(stats, ["current_arousal", "arousal", "emotion_arousal"])
    if valence is None and arousal is None:
        return "中性"

    if arousal is not None and arousal >= 0.35:
        if valence is not None and valence >= 0.1:
            return "快乐激动"
        if valence is not None and valence <= -0.1:
            return "焦虑紧张"
        return "高能量活跃"
    if valence is not None and valence <= -0.2:
        return "低落"
    if valence is not None and valence >= 0.2:
        return "平和愉悦"
    return "平稳"


def _extract_long_term_lines(summary_text: str) -> list[str]:
    if not summary_text:
        return []
    ignored_keywords = ("当前心情", "情绪价", "唤醒度", "最近心情", "短期心情")
    lines: list[str] = []
    for raw in summary_text.splitlines():
        line = raw.strip()
        if not line or line in ("用户画像:", "用户画像"):
            continue
        if any(keyword in line for keyword in ignored_keywords):
            continue
        lines.append(line.lstrip("- ").strip())
    return lines


class UnifiedServiceClient:
    def __init__(self, base_url: str = "http://tag.zrj666.cn:8003"):
        self.base_url = base_url.rstrip("/")

    def get_user_persona_data(self, user_id: str) -> Optional[Dict[str, Any]]:
        try:
            response = requests.get(
                f"{self.base_url}/api/persona/user/{user_id}", timeout=5
            )
            if response.status_code == 200:
                data = response.json()
                if data.get("success"):
                    return data.get("data") or {}
            logger.warning("Failed to fetch persona: HTTP %s", response.status_code)
            return None
        except requests.exceptions.Timeout:
            logger.error("Fetch persona timeout: %s", user_id)
            return None
        except Exception as e:
            logger.error("Fetch persona error: %s", e)
            return None

    def get_user_persona(self, user_id: str) -> Optional[str]:
        persona_data = self.get_user_persona_data(user_id) or {}
        summary_text = str(persona_data.get("summary_text") or "").strip()
        return summary_text or None

    def get_emotion_stats(self, user_id: str) -> Optional[Dict]:
        try:
            response = requests.get(
                f"{self.base_url}/api/statistics/user/{user_id}", timeout=5
            )
            if response.status_code == 200:
                data = response.json()
                if data.get("success"):
                    payload = data.get("data") or {}
                    if (
                        isinstance(payload, dict)
                        and isinstance(payload.get("emotion_preferences"), dict)
                    ):
                        # New schema: { data: { emotion_preferences: {...} } }
                        return payload.get("emotion_preferences") or {}
                    return payload
            return None
        except Exception as e:
            logger.error("Fetch emotion stats failed: %s", e)
            return None

    def get_most_played_song(self, user_id: str) -> Optional[Dict]:
        try:
            persona = self.get_user_persona(user_id)
            if persona:
                return {"title": "from_persona"}
            return None
        except Exception as e:
            logger.error("Fetch most played song failed: %s", e)
            return None

    def build_layered_persona(self, user_id: str) -> Dict[str, Any]:
        """Build persona with short-term mood + long-term profile."""
        if not user_id:
            return {}

        stats = self.get_emotion_stats(user_id) or {}
        persona_data = self.get_user_persona_data(user_id) or {}
        persona_summary = str(persona_data.get("summary_text") or "").strip()

        valence = _pick_float(stats, ["current_valence", "valence", "emotion_valence"])
        arousal = _pick_float(stats, ["current_arousal", "arousal", "emotion_arousal"])
        mood_label = _infer_mood_label(stats)

        short_term = {
            "label": mood_label,
            "valence": valence,
            "arousal": arousal,
            "source": "statistics",
        }

        top_song = _pick_str(
            stats,
            ["top_song_title", "most_played_song", "favorite_song_title"],
        )
        if not top_song:
            top_song = _pick_str(
                persona_data,
                ["top_song_title", "most_played_song", "favorite_song_title"],
            )

        long_term_lines = _extract_long_term_lines(persona_summary)
        if top_song and not any(top_song in line for line in long_term_lines):
            long_term_lines.insert(0, f"最常听歌曲: 《{top_song}》")

        long_term = {
            "summary_lines": long_term_lines[:3],
            "source": "persona",
        }

        short_line = f"短期心情: {mood_label}"
        valence_str = _fmt_float(valence)
        arousal_str = _fmt_float(arousal)
        if valence_str or arousal_str:
            metrics = []
            if valence_str:
                metrics.append(f"情绪价 {valence_str}")
            if arousal_str:
                metrics.append(f"唤醒度 {arousal_str}")
            short_line = f"{short_line} ({', '.join(metrics)})"

        rendered_lines = ["用户画像（短期+长期）:", f"- {short_line}"]
        if long_term_lines:
            for line in long_term_lines[:3]:
                rendered_lines.append(f"- 长期画像: {line}")
        elif persona_summary:
            rendered_lines.append(f"- 长期画像: {persona_summary[:120]}")

        summary_text = "\n".join(rendered_lines).strip()
        return {
            "short_term": short_term,
            "long_term": long_term,
            "summary_text": summary_text,
        }

    def build_music_signals(self, user_id: str) -> Dict:
        """Build normalized weak-evidence music signals for dialogue policy."""
        stats = self.get_emotion_stats(user_id) or {}
        if not stats:
            return {}

        play_candidates = [
            _pick_float(stats, ["recent_play_count"]),
            _pick_float(stats, ["play_count_7d"]),
            _pick_float(stats, ["effective_plays"]),
            _pick_float(stats, ["total_plays"]),
        ]
        recent_play_count = int(
            max([value for value in play_candidates if isinstance(value, (int, float))], default=0)
        )
        low_arousal_ratio = _pick_float(
            stats, ["low_arousal_ratio", "calm_ratio", "sad_calm_ratio"]
        )
        negative_ratio = _pick_float(
            stats, ["negative_ratio", "sad_ratio", "melancholy_ratio"]
        )

        # Compatibility fallback for emotion_preferences schema
        arousal_avg = _pick_float(stats, ["arousal_avg", "current_arousal", "arousal"])
        valence_avg = _pick_float(stats, ["valence_avg", "current_valence", "valence"])
        if low_arousal_ratio is None and arousal_avg is not None:
            # arousal in [-1, 1] -> low_arousal_ratio in [0, 1]
            low_arousal_ratio = _clamp01((1.0 - arousal_avg) / 2.0)
        if negative_ratio is None and valence_avg is not None:
            # valence in [-1, 1] -> negative_ratio in [0, 1]
            negative_ratio = _clamp01((1.0 - valence_avg) / 2.0)

        return {
            "source": "unified_service_stats",
            "has_recent_music_activity": recent_play_count > 0,
            "recent_play_count": recent_play_count,
            "low_arousal_ratio": low_arousal_ratio,
            "negative_ratio": negative_ratio,
            "top_song_title": str(
                stats.get("top_song_title")
                or stats.get("most_played_song")
                or stats.get("favorite_song_title")
                or ""
            ).strip(),
        }
