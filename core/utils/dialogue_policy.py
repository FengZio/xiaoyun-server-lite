import json
import re
from typing import Any, Dict, List

from core.utils.dialogue import Message


INTENT_LABELS = {
    "reflect": "倾听/共情",
    "clarify": "澄清",
    "guide": "轻量引导",
    "advise": "给建议",
    "shift": "转移注意",
    "summarize_followup": "总结与跟进",
}

MICRO_SKILLS = {
    "reflect_label": "听起来你有点{emotion_phrase}。",
    "careful_attribution": "我感觉你可能更像在{state}，我理解得对吗？",
    "choice_question": "你更想{option_a}，还是想{option_b}？",
    "permission_advice": "我可以给你一个很短的办法，你想听吗？",
    "soft_wrapup": "今天先到这也可以，我明天再跟你确认一下{follow_topic}。",
}

HIGH_RISK_KEYWORDS = {
    "自杀", "自残", "不想活", "结束生命", "伤害自己", "轻生", "割腕", "跳楼",
}

LOW_MOOD_KEYWORDS = {
    "难受", "崩溃", "焦虑", "抑郁", "疲惫", "失眠", "累", "空虚", "孤独", "没意思",
}

ADVISE_HINT_KEYWORDS = {
    "怎么做", "怎么办", "方法", "建议", "步骤", "解决", "帮我想", "计划",
}

SHIFT_HINT_KEYWORDS = {
    "换个话题", "聊点轻松", "不想聊这个", "算了", "先不说了",
}

MUSIC_HINT_KEYWORDS = {"歌", "音乐", "歌单", "听歌", "电台", "晚安"}


def _contains_any(text: str, keywords: set[str]) -> bool:
    return any(word in text for word in keywords)


def _is_explicit_advice_request(query: str) -> bool:
    q = query or ""
    return any(k in q for k in ("怎么做", "怎么办", "方法", "步骤", "建议", "解决"))


def _wants_checklist_output(query: str) -> bool:
    q = query or ""
    return any(k in q for k in ("分点", "清单", "步骤", "列出来", "一二三", "123", "逐条"))


def _normalize_reply_text(query: str) -> str:
    text = (query or "").strip().lower()
    for ch in ("，", "。", "！", "？", ",", ".", "!", "?", "、", "；", ";", "：", ":"):
        text = text.replace(ch, "")
    return text


def _is_affirmative_reply(query: str) -> bool:
    text = _normalize_reply_text(query)
    if not text:
        return False
    positive = (
        "是",
        "好",
        "好的",
        "行",
        "可以",
        "想",
        "要",
        "嗯",
        "对",
        "同意",
        "继续",
        "请说",
        "说吧",
        "来吧",
    )
    negative = ("不", "不要", "不用", "不想", "不需要", "算了", "先不用", "别")
    if any(token in text for token in negative):
        return False
    return any(token in text for token in positive)


def _is_negative_reply(query: str) -> bool:
    text = _normalize_reply_text(query)
    if not text:
        return False
    negative = ("不", "不要", "不用", "不想", "不需要", "算了", "先不用", "别", "先这样")
    positive = ("可以", "想", "要", "继续", "同意")
    if any(token in text for token in positive):
        return False
    return any(token in text for token in negative)


def _extract_query_terms(query: str) -> List[str]:
    tokens = re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,}", (query or "").lower())
    seen = set()
    terms = []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            terms.append(t)
    return terms


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


def _extract_memory_candidates(memory_str: str) -> List[str]:
    if not memory_str:
        return []
    text = memory_str.strip()
    if not text:
        return []

    try:
        parsed = json.loads(text)
        lines = _flatten_json(parsed)
    except Exception:
        lines = [line.strip("- ").strip() for line in text.splitlines() if line.strip()]

    normalized = []
    for line in lines:
        normalized.append((line[:220] + "...") if len(line) > 220 else line)
    return normalized


def _select_memory_items(query: str, memory_str: str, limit: int = 3) -> List[str]:
    candidates = _extract_memory_candidates(memory_str)
    if not candidates:
        return []

    terms = _extract_query_terms(query)
    scored: List[tuple[int, int, str]] = []
    for idx, item in enumerate(candidates):
        score = 0
        item_lower = item.lower()
        for term in terms:
            if term in item_lower:
                score += 3
        if any(k in item for k in ("最近", "今天", "本周", "待响应", "潜在关怀")):
            score += 1
        scored.append((score, -idx, item))

    scored.sort(reverse=True)
    top = [x[2] for x in scored[: max(1, limit)]]
    seen = set()
    result = []
    for item in top:
        if item not in seen:
            seen.add(item)
            result.append(item)
        if len(result) >= limit:
            break
    return result


def _detect_risk_level(query: str) -> str:
    text = (query or "").lower()
    if _contains_any(text, HIGH_RISK_KEYWORDS):
        return "high_risk"
    if _contains_any(text, LOW_MOOD_KEYWORDS):
        return "low_mood"
    return "normal"


def _select_intent(query: str, risk_level: str) -> str:
    text = (query or "").lower()
    if risk_level == "high_risk":
        return "reflect"
    if _contains_any(text, SHIFT_HINT_KEYWORDS):
        return "shift"
    if _contains_any(text, ADVISE_HINT_KEYWORDS):
        return "advise"
    if "?" in text or "？" in text:
        return "clarify"
    if risk_level == "low_mood":
        return "reflect"
    return "guide"


def _apply_intent_rhythm(intent: str, last_intent: str | None, query: str) -> str:
    suggestion_intents = {"guide", "advise"}
    if intent not in suggestion_intents or last_intent not in suggestion_intents:
        return intent
    if _contains_any((query or "").lower(), ADVISE_HINT_KEYWORDS):
        return "advise"
    return "clarify"


def _select_style(intent: str, risk_level: str) -> Dict[str, str | int]:
    if risk_level in ("low_mood", "high_risk"):
        return {
            "tone": "gentle",
            "length": "short",
            "question_density": "low",
            "initiative": "low",
            "suggestion_strength": 0 if risk_level == "high_risk" else 1,
        }
    if intent == "advise":
        return {
            "tone": "neutral",
            "length": "medium",
            "question_density": "low",
            "initiative": "high",
            "suggestion_strength": 2,
        }
    if intent == "clarify":
        return {
            "tone": "gentle",
            "length": "short",
            "question_density": "high",
            "initiative": "low",
            "suggestion_strength": 0,
        }
    return {
        "tone": "gentle",
        "length": "short",
        "question_density": "low",
        "initiative": "low",
        "suggestion_strength": 1,
    }


def _select_micro_skill(intent: str, risk_level: str) -> str:
    if risk_level == "high_risk":
        return "careful_attribution"
    if intent == "reflect":
        return "reflect_label"
    if intent == "clarify":
        return "choice_question"
    if intent == "advise":
        return "permission_advice"
    if intent == "summarize_followup":
        return "soft_wrapup"
    return "careful_attribution"


def _select_micro_skill_with_state(
    intent: str,
    risk_level: str,
    explicit_advice_request: bool,
    policy_state: Dict[str, Any] | None,
) -> str:
    state = policy_state or {}
    permission_cooldown = int(state.get("permission_advice_cooldown_turns") or 0)
    blocked_micro_skill = str(state.get("blocked_micro_skill") or "")
    blocked_micro_skill_turns = int(state.get("blocked_micro_skill_turns") or 0)
    if intent == "advise":
        if explicit_advice_request or permission_cooldown > 0:
            return "none"
    selected = _select_micro_skill(intent, risk_level)
    if blocked_micro_skill_turns > 0 and selected == blocked_micro_skill:
        return "none"
    return selected


def _fallback_topic(query: str) -> str:
    terms = _extract_query_terms(query or "")
    if terms:
        return terms[0][:16]
    q = (query or "").strip()
    if not q:
        return "这件事"
    return q[:16]


def _micro_skill_slots(
    micro_skill_id: str,
    query: str,
    intent: str,
    risk_level: str,
    hypothesis: Dict[str, Any],
    explicit_advice_request: bool,
) -> Dict[str, str]:
    label = str(hypothesis.get("label", "") or "")
    if not label or label == "中性":
        label = "有点累"
    topic = _fallback_topic(query)

    option_a = "先把现在最卡的点说清楚"
    option_b = "我先给你一个可执行的小步骤"
    if explicit_advice_request:
        option_a = "先给你一个最快能做的步骤"
        option_b = "按场景给你一个更稳的方案"
    elif intent == "clarify":
        option_a = "先说感受"
        option_b = "先说你想要的结果"

    follow_topic = topic if topic else "这件事"
    if risk_level in ("low_mood", "high_risk"):
        follow_topic = "你的状态"

    return {
        "emotion_phrase": label,
        "state": label,
        "option_a": option_a,
        "option_b": option_b,
        "follow_topic": follow_topic,
    }


def _render_micro_skill_text(
    micro_skill_id: str,
    query: str,
    intent: str,
    risk_level: str,
    hypothesis: Dict[str, Any],
    explicit_advice_request: bool,
) -> str:
    template = MICRO_SKILLS.get(micro_skill_id, "")
    if not template:
        return ""
    slots = _micro_skill_slots(
        micro_skill_id=micro_skill_id,
        query=query,
        intent=intent,
        risk_level=risk_level,
        hypothesis=hypothesis,
        explicit_advice_request=explicit_advice_request,
    )
    try:
        return template.format(**slots)
    except Exception:
        return template


def _build_emotion_hypothesis(query: str, risk_level: str) -> Dict[str, Any]:
    text = query or ""
    if risk_level == "high_risk":
        return {"label": "强烈低落", "confidence": 0.75, "source": "text"}
    if "疲惫" in text or "累" in text:
        return {"label": "疲惫", "confidence": 0.68, "source": "text"}
    if "难过" in text or "伤心" in text:
        return {"label": "难过", "confidence": 0.66, "source": "text"}
    return {"label": "中性", "confidence": 0.4, "source": "unknown"}


def _read_numeric_signal(data: Dict[str, Any], keys: List[str]) -> float | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _build_music_hypothesis(music_signals: Dict[str, Any] | None) -> Dict[str, Any]:
    signals = music_signals or {}
    if not isinstance(signals, dict) or not signals:
        return {"label": "none", "confidence": 0.0, "source": "none", "evidence": []}

    low_arousal_ratio = _read_numeric_signal(
        signals, ["low_arousal_ratio", "calm_ratio", "sad_calm_ratio"]
    )
    negative_ratio = _read_numeric_signal(
        signals, ["negative_ratio", "sad_ratio", "melancholy_ratio"]
    )
    has_recent_activity = bool(
        signals.get("has_recent_music_activity")
        or (signals.get("recent_play_count") or 0) > 0
    )
    top_song_title = str(signals.get("top_song_title") or "").strip()

    confidence = 0.0
    label = "neutral"
    evidence: List[str] = []
    if has_recent_activity:
        confidence += 0.15
        evidence.append("recent_music_activity")
    if low_arousal_ratio is not None and low_arousal_ratio >= 0.6:
        confidence += 0.3
        label = "low_arousal"
        evidence.append(f"low_arousal_ratio={low_arousal_ratio:.2f}")
    if negative_ratio is not None and negative_ratio >= 0.55:
        confidence += 0.3
        label = "low_mood_tendency"
        evidence.append(f"negative_ratio={negative_ratio:.2f}")
    if top_song_title:
        confidence += 0.05
        evidence.append("top_song_available")

    confidence = min(0.85, round(confidence, 2))
    source = str(signals.get("source") or "music_stats")
    return {
        "label": label,
        "confidence": confidence,
        "source": source,
        "evidence": evidence,
        "top_song_title": top_song_title,
    }


def _music_role(query: str, music_hypothesis: Dict[str, Any] | None = None) -> str:
    text = (query or "").lower()
    if _contains_any(text, MUSIC_HINT_KEYWORDS):
        if any(k in query for k in ("推荐", "来一首", "电台")):
            return "action"
        if any(k in query for k in ("为什么", "感觉")):
            return "empathy_medium"
        return "opening"

    hypothesis = music_hypothesis or {}
    confidence = float(hypothesis.get("confidence") or 0.0)
    if confidence < 0.5:
        return "none"
    if hypothesis.get("label") in ("low_arousal", "low_mood_tendency"):
        return "empathy_medium"
    return "opening"


def _music_soft_confirmation_text(music_hypothesis: Dict[str, Any], music_role: str) -> str:
    if music_role not in ("opening", "empathy_medium"):
        return ""
    if float(music_hypothesis.get("confidence") or 0.0) < 0.5:
        return ""
    title = str(music_hypothesis.get("top_song_title") or "").strip()
    if title:
        return f"你最近常听《{title}》，是想先放空一下吗？"
    return "你最近听歌风格偏低唤醒，是想先缓一缓吗？"


def _recent_user_text(dialogue: List[Message], turns: int = 3) -> str:
    snippets = []
    for msg in reversed(dialogue):
        if msg.role == "user" and msg.content:
            snippets.append(msg.content)
            if len(snippets) >= turns:
                break
    snippets.reverse()
    return "\n".join(snippets)


def _extract_recent_episodic(
    dialogue: List[Message], limit: int = 3, current_user_text: str | None = None
) -> List[str]:
    events: List[str] = []
    skipped_current = False
    for msg in reversed(dialogue):
        if msg.role == "user" and msg.content:
            raw_text = msg.content.strip()
            if current_user_text and not skipped_current and raw_text == current_user_text.strip():
                skipped_current = True
                continue
            text = raw_text
            if len(text) > 80:
                text = text[:80] + "..."
            if text:
                events.append(f"近期事件: {text}")
        if len(events) >= limit:
            break
    events.reverse()
    return events


def _strip_memory_prefix(text: str) -> str:
    if not text:
        return ""
    cleaned = text.strip()
    for prefix in ("近期事件:", "未完话题:"):
        if cleaned.startswith(prefix):
            return cleaned[len(prefix):].strip()
    return cleaned


def _nearby_dedupe(items: List[str]) -> List[str]:
    if not items:
        return []
    deduped: List[str] = []
    prev = ""
    prev_tokens: List[str] = []
    for item in items:
        cur = item or ""
        cur_tokens = _extract_query_terms(cur)
        if not prev:
            deduped.append(cur)
            prev = cur
            prev_tokens = cur_tokens
            continue
        if prev_tokens and cur_tokens:
            overlap = len(set(prev_tokens) & set(cur_tokens)) / max(
                1, max(len(prev_tokens), len(cur_tokens))
            )
        else:
            overlap = 0.0
        if overlap >= 0.8:
            # 保留信息量更大的那条
            if len(cur) > len(prev):
                deduped[-1] = cur
                prev = cur
                prev_tokens = cur_tokens
            continue
        deduped.append(cur)
        prev = cur
        prev_tokens = cur_tokens
    return deduped


def _pack_memory_items(
    query: str,
    stable_items: List[str],
    episodic_items: List[str],
    long_memory_items: List[str],
    limit: int = 3,
    current_user_text: str | None = None,
) -> List[Dict[str, str]]:
    query_terms = _extract_query_terms(query)
    source_caps = {"stable": 2, "episodic": 3, "long_term": 3}
    source_count = {"stable": 0, "episodic": 0, "long_term": 0}
    candidates: List[tuple[int, int, str, str]] = []

    def add_candidates(source: str, items: List[str], offset: int) -> None:
        items = _nearby_dedupe(items)
        for idx, item in enumerate(items):
            if not item:
                continue
            summary = item.strip()
            if not summary:
                continue
            if current_user_text and _strip_memory_prefix(summary) == current_user_text.strip():
                continue
            is_unfinished = source == "episodic" and summary.startswith("未完话题:")
            is_recent = source == "episodic" and summary.startswith("近期事件:")
            related = any(term in summary.lower() for term in query_terms)

            if is_unfinished:
                priority = 300
            elif related:
                priority = 220
            elif is_recent:
                priority = 120
            elif source == "stable":
                priority = 110
            else:
                priority = 100
            candidates.append((priority, -(offset + idx), source, summary))

    add_candidates("episodic", episodic_items, offset=0)
    add_candidates("stable", stable_items, offset=100)
    add_candidates("long_term", long_memory_items, offset=200)

    candidates.sort(reverse=True)
    packed: List[Dict[str, str]] = []
    seen = set()
    for _, _, source, summary in candidates:
        if summary in seen:
            continue
        if source_count[source] >= source_caps[source]:
            continue
        seen.add(summary)
        source_count[source] += 1
        packed.append({"source": source, "summary": summary})
        if len(packed) >= max(1, limit):
            break
    return packed


def build_dialogue_policy(
    query: str,
    dialogue: List[Message],
    memory_str: str,
    config: Dict[str, Any],
    stable_memory_items: List[str] | None = None,
    episodic_memory_items: List[str] | None = None,
    last_intent: str | None = None,
    music_signals: Dict[str, Any] | None = None,
    policy_state: Dict[str, Any] | None = None,
    current_user_text: str | None = None,
) -> Dict[str, Any]:
    policy_conf = config.get("dialogue_policy", {})
    max_memory_items = int(policy_conf.get("max_memory_items", 3))
    enable_micro_skills = bool(policy_conf.get("enable_micro_skills", True))

    incoming_state = dict(policy_state or {})
    pending_confirmation = str(incoming_state.get("pending_confirmation") or "")
    permission_cooldown = max(
        0, int(incoming_state.get("permission_advice_cooldown_turns") or 0) - 1
    )
    music_cooldown = max(
        0, int(incoming_state.get("music_confirmation_cooldown_turns") or 0) - 1
    )
    blocked_micro_skill = str(incoming_state.get("blocked_micro_skill") or "")
    blocked_micro_skill_turns = max(
        0, int(incoming_state.get("blocked_micro_skill_turns") or 0) - 1
    )
    confirmation_status = "none"

    user_confirmed = pending_confirmation and _is_affirmative_reply(query)
    user_rejected = pending_confirmation and _is_negative_reply(query)
    if user_confirmed:
        confirmation_status = f"confirmed_{pending_confirmation}"
    elif user_rejected:
        confirmation_status = f"rejected_{pending_confirmation}"

    if pending_confirmation == "permission_advice" and (user_confirmed or user_rejected):
        permission_cooldown = max(permission_cooldown, 2)
    if pending_confirmation == "music_soft_confirmation" and (user_confirmed or user_rejected):
        music_cooldown = max(music_cooldown, 2)
    if pending_confirmation and (query or "").strip():
        blocked_micro_skill = pending_confirmation
        blocked_micro_skill_turns = max(blocked_micro_skill_turns, 1)

    risk_level = _detect_risk_level(query)
    intent = _apply_intent_rhythm(
        _select_intent(query, risk_level), last_intent=last_intent, query=query
    )
    if pending_confirmation == "permission_advice" and user_confirmed:
        intent = "advise"
    style = _select_style(intent, risk_level)
    stable_items = stable_memory_items or []
    episodic_items = episodic_memory_items or _extract_recent_episodic(
        dialogue, limit=3, current_user_text=current_user_text or query
    )
    long_memory_items = _select_memory_items(query, memory_str, limit=max_memory_items)

    memory_items = _pack_memory_items(
        query=query,
        stable_items=stable_items,
        episodic_items=episodic_items,
        long_memory_items=long_memory_items,
        limit=max_memory_items,
        current_user_text=current_user_text or query,
    )

    emotion_hypothesis = _build_emotion_hypothesis(query, risk_level)
    enable_music_hypothesis = bool(policy_conf.get("enable_music_hypothesis", True))
    music_hypothesis = (
        _build_music_hypothesis(music_signals) if enable_music_hypothesis else {}
    )
    music_role = _music_role(query, music_hypothesis)
    music_soft_confirmation = _music_soft_confirmation_text(music_hypothesis, music_role)
    if music_cooldown > 0:
        music_soft_confirmation = ""
    music_need_confirmation = bool(music_soft_confirmation)

    explicit_advice_request = _is_explicit_advice_request(query)
    wants_checklist_output = _wants_checklist_output(query)
    effective_state = {
        "permission_advice_cooldown_turns": permission_cooldown,
        "music_confirmation_cooldown_turns": music_cooldown,
        "blocked_micro_skill": blocked_micro_skill,
        "blocked_micro_skill_turns": blocked_micro_skill_turns,
    }
    micro_skill_id = (
        _select_micro_skill_with_state(
            intent=intent,
            risk_level=risk_level,
            explicit_advice_request=explicit_advice_request,
            policy_state=effective_state,
        )
        if enable_micro_skills
        else "none"
    )
    micro_skill_template = MICRO_SKILLS.get(micro_skill_id, "") if enable_micro_skills else ""
    micro_skill_text = (
        _render_micro_skill_text(
            micro_skill_id=micro_skill_id,
            query=query,
            intent=intent,
            risk_level=risk_level,
            hypothesis=emotion_hypothesis,
            explicit_advice_request=explicit_advice_request,
        )
        if enable_micro_skills
        else ""
    )

    need_emotion_confirmation = (
        intent in ("reflect", "clarify") and emotion_hypothesis["confidence"] < 0.8
    )
    need_permission_confirmation = micro_skill_id == "permission_advice"
    need_confirmation = (
        need_emotion_confirmation or music_need_confirmation or need_permission_confirmation
    )

    next_pending_confirmation = ""
    if need_permission_confirmation:
        next_pending_confirmation = "permission_advice"
    elif music_need_confirmation:
        next_pending_confirmation = "music_soft_confirmation"
    elif need_emotion_confirmation and micro_skill_id not in ("none", ""):
        next_pending_confirmation = micro_skill_id
    elif need_emotion_confirmation:
        next_pending_confirmation = "emotion_hypothesis"

    if pending_confirmation and (user_confirmed or user_rejected):
        next_pending_confirmation = next_pending_confirmation or ""

    next_policy_state = {
        "pending_confirmation": next_pending_confirmation,
        "permission_advice_cooldown_turns": permission_cooldown,
        "music_confirmation_cooldown_turns": music_cooldown,
        "blocked_micro_skill": blocked_micro_skill,
        "blocked_micro_skill_turns": blocked_micro_skill_turns,
        "last_confirmation_status": confirmation_status,
    }

    flags = ["no_diagnosis", "no_professional_substitute", "no_overpromise"]
    if risk_level in ("low_mood", "high_risk"):
        flags.append("support_first")

    return {
        "intent": intent,
        "intent_label": INTENT_LABELS.get(intent, intent),
        "tone": style["tone"],
        "length": style["length"],
        "question_density": style["question_density"],
        "initiative": style["initiative"],
        "suggestion_strength": style["suggestion_strength"],
        "music_role": music_role,
        "music_hypothesis": music_hypothesis,
        "music_soft_confirmation": music_soft_confirmation,
        "micro_skill_id": micro_skill_id,
        "micro_skill_template": micro_skill_template,
        "micro_skill_text": micro_skill_text,
        "stable_memories": stable_items[:2],
        "episodic_memories": episodic_items[:3],
        "memory_to_inject": memory_items[:3],
        "memory_to_inject_text": [item["summary"] for item in memory_items[:3]],
        "explicit_advice_request": explicit_advice_request,
        "wants_checklist_output": wants_checklist_output,
        "emotion_hypothesis": emotion_hypothesis,
        "need_confirmation": need_confirmation,
        "confirmation_status": confirmation_status,
        "next_policy_state": next_policy_state,
        "risk_level": risk_level,
        "boundary_flags": flags,
        "recent_user_context": _recent_user_text(dialogue),
    }


def render_policy_constraints(policy: Dict[str, Any]) -> str:
    memory_lines = ""
    if policy.get("memory_to_inject"):
        rows = []
        for item in policy["memory_to_inject"]:
            if isinstance(item, dict):
                rows.append(f"- ({item.get('source', 'unknown')}) {item.get('summary', '')}")
            else:
                rows.append(f"- {item}")
        memory_lines = "\n".join(rows)
    else:
        memory_lines = "- 无"

    confirmation_hint = "需要先做软确认，再推进。" if policy.get("need_confirmation") else "可直接继续对话。"
    high_risk_hint = (
        "当前疑似高风险，先陪伴并鼓励联系现实支持资源，不给激进方案。"
        if policy.get("risk_level") == "high_risk"
        else "如用户低落，先陪伴后建议。"
    )
    music_soft_confirmation_hint = policy.get("music_soft_confirmation") or "none"
    confirmation_status = policy.get("confirmation_status") or "none"
    permission_followup_hint = (
        "用户刚确认愿意听建议，本轮直接给建议，不要再次询问是否需要建议。"
        if confirmation_status == "confirmed_permission_advice"
        else "按当前策略继续。"
    )

    return f"""

[Dialogue Policy]
- 本轮意图: {policy.get("intent_label")}
- 语气: {policy.get("tone")}
- 长度: {policy.get("length")}
- 提问密度: {policy.get("question_density")}
- 主动性: {policy.get("initiative")}
- 建议强度: {policy.get("suggestion_strength")}
- 音乐使用方式: {policy.get("music_role")}
- 音乐假设: {policy.get("music_hypothesis")}
- 音乐软确认: {music_soft_confirmation_hint}
- 微技能: {policy.get("micro_skill_id")}
- 微技能模板: {policy.get("micro_skill_template")}
- 微技能填槽结果: {policy.get("micro_skill_text")}
- 情绪假设: {policy.get("emotion_hypothesis")}
- 确认策略: {confirmation_hint}
- 确认结果: {confirmation_status}

[记忆精注入(仅本轮)]
{memory_lines}

[边界策略]
- 不做心理诊断，不贴人格标签。
- 不替代医疗/心理/法律等专业帮助。
- 语气温柔但不过度承诺。
- 音乐和画像只当弱证据，必须可被用户纠正。
- {high_risk_hint}

[生成约束]
- 禁止重复句和口头禅堆叠。
- 禁止编造与用户无关的人设故事。
- 自然口语优先，最多 2~3 个建议点。
- 用户明确问“怎么做/方法/步骤”时，直接给可执行建议。
- 若用户刚确认“想听建议”，本轮直接给建议，不再二次确认。
- 仅当用户明确要求清单时，使用编号列表。
- 当前是否要求分点输出: {policy.get("wants_checklist_output")}
- 确认后续动作: {permission_followup_hint}
"""
