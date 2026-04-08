import os
import re
import yaml
from typing import Dict, List, Any

from config.config_loader import get_project_dir


class StableMemoryStore:
    """稳定偏好记忆存储（本地 YAML，低频更新）。"""

    def __init__(self, path: str = None):
        self.path = path or os.path.join(get_project_dir(), "data", ".stable_memory.yaml")
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

    def _default_profile(self) -> Dict[str, Any]:
        return {
            "nickname": "",
            "reply_length": "",  # short|medium|long
            "suggestion_style": "",  # practical|comfort
            "taboo": [],
            "music_preference": "",
        }

    def get_profile(self, user_id: str) -> Dict[str, Any]:
        if user_id not in self._cache:
            self._cache[user_id] = self._default_profile()
        return self._cache[user_id]

    @staticmethod
    def _looks_like_name_question(text: str) -> bool:
        t = (text or "").strip()
        if not t:
            return False
        question_markers = ("?", "？", "吗", "么", "嘛", "还记得", "记得我", "叫什么")
        return any(marker in t for marker in question_markers)

    @staticmethod
    def _normalize_nickname(raw: str) -> str:
        nick = (raw or "").strip()
        # 去掉常见语气词尾，避免“我叫小张呢”被写成“小张呢”
        nick = re.sub(r"(呢|呀|啊|嘛|呐|哦|啦)+$", "", nick)
        return nick.strip()

    def update_from_user_text(self, user_id: str, text: str) -> bool:
        if not user_id or not text:
            return False

        profile = self.get_profile(user_id)
        changed = False
        text_lower = text.lower()

        # 称呼偏好：问句不更新，避免“我叫什么还记得我吗”误写入
        if not self._looks_like_name_question(text):
            nickname_patterns = [
                r"(?:叫我|喊我|称呼我)\s*([\u4e00-\u9fa5A-Za-z0-9_]{1,12})",
                r"(?:你可以叫我|以后叫我)\s*([\u4e00-\u9fa5A-Za-z0-9_]{1,12})",
                r"(?:我叫(?:做)?|我的名字是|我名字是)\s*([\u4e00-\u9fa5A-Za-z0-9_]{1,12})",
            ]
            for pattern in nickname_patterns:
                m = re.search(pattern, text)
                if m:
                    nick = self._normalize_nickname(m.group(1))
                    if nick and profile.get("nickname") != nick:
                        profile["nickname"] = nick
                        changed = True
                    break

        explicit_reply_pref = any(k in text for k in ("回复", "回答", "说话", "讲解"))
        explicit_suggestion_pref = any(k in text for k in ("建议", "方法", "安慰", "说教", "鸡汤"))
        explicit_music_pref = any(k in text for k in ("音乐", "歌", "歌单", "听歌", "风格"))
        explicit_taboo = "别提" in text or "不要提" in text or "别聊" in text or "不要聊" in text

        # 回复长度偏好（仅在明确表达“回复风格”时更新）
        if explicit_reply_pref and any(k in text for k in ("短一点", "简短", "短回复", "别太长")):
            if profile.get("reply_length") != "short":
                profile["reply_length"] = "short"
                changed = True
        elif explicit_reply_pref and any(k in text for k in ("详细点", "长一点", "展开说", "多说点")):
            if profile.get("reply_length") != "long":
                profile["reply_length"] = "long"
                changed = True

        # 建议风格偏好（仅在明确表达“建议方式”时更新）
        if explicit_suggestion_pref and any(
            k in text for k in ("不要鸡汤", "别鸡汤", "少说鸡汤", "直接给方法", "别说教")
        ):
            if profile.get("suggestion_style") != "practical":
                profile["suggestion_style"] = "practical"
                changed = True
        elif explicit_suggestion_pref and any(k in text for k in ("多安慰我", "先安慰", "温柔一点")):
            if profile.get("suggestion_style") != "comfort":
                profile["suggestion_style"] = "comfort"
                changed = True

        # 禁忌
        if explicit_taboo:
            taboo = text
            for marker in ("别提", "不要提", "别聊", "不要聊"):
                if marker in text:
                    taboo = text.split(marker, 1)[1]
                    break
            taboo = taboo.strip()[:30]
            if taboo and taboo not in profile["taboo"]:
                profile["taboo"].append(taboo)
                profile["taboo"] = profile["taboo"][-5:]
                changed = True

        # 长期音乐偏好（仅在明确表达“音乐偏好”时更新）
        if explicit_music_pref and any(k in text_lower for k in ("安静的歌", "轻音乐", "舒缓", "钢琴")):
            if profile.get("music_preference") != "安静舒缓":
                profile["music_preference"] = "安静舒缓"
                changed = True
        elif explicit_music_pref and any(k in text_lower for k in ("摇滚", "电子", "节奏快")):
            if profile.get("music_preference") != "高能节奏":
                profile["music_preference"] = "高能节奏"
                changed = True

        if changed:
            self._cache[user_id] = profile
            self._save_all()
        return changed

    def query_relevant(self, user_id: str, query: str, limit: int = 2) -> List[str]:
        profile = self.get_profile(user_id)
        if not profile:
            return []

        items: List[str] = []
        if profile.get("nickname"):
            items.append(f"称呼偏好: 用户希望被称呼为“{profile['nickname']}”")
        if profile.get("reply_length"):
            items.append(f"回复长度偏好: {profile['reply_length']}")
        if profile.get("suggestion_style"):
            items.append(f"建议风格偏好: {profile['suggestion_style']}")
        if profile.get("music_preference"):
            items.append(f"长期音乐偏好: {profile['music_preference']}")
        for taboo in profile.get("taboo", [])[:2]:
            items.append(f"禁忌话题: 尽量避免提及“{taboo}”")

        if not items:
            return []

        if not query:
            return items[:limit]

        query_lower = query.lower()
        scored = []
        for idx, item in enumerate(items):
            score = 0
            item_lower = item.lower()
            for token in re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,}", query_lower):
                if token in item_lower:
                    score += 3
            if "音乐" in query and "音乐偏好" in item:
                score += 2
            if any(k in query for k in ("怎么说", "语气", "回复")) and "回复长度偏好" in item:
                score += 2
            scored.append((score, -idx, item))

        scored.sort(reverse=True)
        return [x[2] for x in scored[: max(1, limit)]]
