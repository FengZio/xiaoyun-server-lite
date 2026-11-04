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
    # 注意：程度词（有点/挺）已并入 emotion_phrase 槽位，模板内不要再写"有点"，
    # 否则会拼出"听起来你有点有点累。"（历史 bug，见 2026-11-02 归因记录）
    "reflect_label": "听起来你{emotion_phrase}。",
    "careful_attribution": "我感觉你可能更像在{state}，我理解得对吗？",
    "choice_question": "你更想{option_a}，还是想{option_b}？",
    "permission_advice": "我可以给你一个很短的办法，你想听吗？",
    "guide_step": "先试一个很小的动作：{guide_step}",
    "silent_company_opening": "我先安静陪你待一会儿，不追问；如果你想说了，只要回我一句就行。",
    "soft_wrapup": "今天先到这也可以，我明天再跟你确认一下{follow_topic}。",
}

HIGH_RISK_KEYWORDS = {
    "自杀", "自残", "不想活", "结束生命", "伤害自己", "轻生", "割腕", "跳楼",
}

# 每个模板必需的非空槽位；任一为空则不渲染该微技能句，
# 避免出现"听起来你有点有点累。"这类与用户输入无关的兜底成品句。
MICRO_SKILL_REQUIRED_SLOTS = {
    "reflect_label": ("emotion_phrase",),
    "careful_attribution": ("state",),
    "choice_question": ("option_a", "option_b"),
    "guide_step": ("guide_step",),
    "soft_wrapup": ("follow_topic",),
}

# 情绪标签 → 可直接嵌进"听起来你{emotion_phrase}。"的自然口语片段（含程度词）
EMOTION_PHRASES = {
    "疲惫": "有点累",
    "难过": "心里挺不好受的",
    "憋闷": "心里堵得慌",
    "焦虑": "有点不安",
    "撑不住": "真的有点撑不住了",
    "烦躁": "有点烦躁",
    "委屈": "挺委屈的",
    "孤单": "有点孤单",
    "睡不好": "最近睡得不踏实",
    "压力大": "压力挺大的",
    "生气": "是真的生气了",
}

# 负面情绪词典：命中即认为有明确负面情绪（标签, 命中关键词）
NEGATIVE_EMOTION_CUES = [
    ("疲惫", ("疲惫", "累", "没力气", "撑不住劲")),
    ("难过", ("难过", "伤心", "想哭", "失落", "低落")),
    ("憋闷", ("堵得慌", "心里堵", "憋", "闷得慌")),
    ("焦虑", ("焦虑", "紧张", "慌", "不安", "担心")),
    ("撑不住", ("崩溃", "撑不住", "受不了了", "扛不住")),
    ("烦躁", ("烦", "火大", "暴躁", "气死")),
    ("委屈", ("委屈", "不公平")),
    ("孤单", ("孤独", "孤单", "没人")),
    ("睡不好", ("失眠", "睡不着", "做噩梦")),
    ("压力大", ("压力", "压得", "忙不过来")),
    ("生气", ("生气", "吵架", "吵了一架")),
]

# 正面情绪线索：命中则不再使用"共情标注"类微技能（那是错配）
POSITIVE_EMOTION_CUES = (
    "还行", "不错", "挺好", "好多了", "进步", "开心", "高兴", "顺利",
    "考得还行", "比上次好", "成功了", "搞定了", "通过了",
)

# 高风险场景的独立约束块：不复述、不软化、不反问确认、给出现实求助路径。
# 历史问题：high_risk 曾复用 careful_attribution，输出"我感觉你可能更像在强烈低落，
# 我理解得对吗？"——把自杀意念降级成"低落"，还把确认责任推回用户，比不加决策层更危险。
HIGH_RISK_GUIDANCE = """
[高风险响应要求]
- 用户表达可能涉及自伤或自杀风险。这是安全优先场景，不是普通情绪倾诉。
- 必须先明确表达"我在意你现在很危险"，再谈其他。
- 不要分析原因，不要贴标签，不要说"你可能只是低落/想多了"这类弱化表述。
- 不要用"我理解得对吗"把确认责任推回用户。
- 不要给任何具体方法、手段、或暗示"解脱/结束"的表述。
- 直接鼓励立刻联系可信任的人陪在身边，并给出专业求助路径：
  全国统一心理援助热线 12356；北京心理危机干预热线 010-82951332。
- 若存在即刻危险，明确建议立即前往就近急诊或拨打 120。
""".strip()

LOW_MOOD_KEYWORDS = {
    "难受", "崩溃", "焦虑", "抑郁", "疲惫", "失眠", "累", "空虚", "孤独", "没意思",
}

ADVISE_HINT_KEYWORDS = {
    "怎么做", "怎么办", "方法", "建议", "步骤", "解决", "帮我想", "计划",
}

GUIDE_HINT_KEYWORDS = {
    "怎么做", "怎么办", "方法", "步骤", "解决", "帮我想", "计划", "怎么缓解",
}

SHIFT_HINT_KEYWORDS = {
    "换个话题", "聊点轻松", "不想聊这个", "算了", "先不说了",
}

MUSIC_HINT_KEYWORDS = {"歌", "音乐", "歌单", "听歌", "电台", "晚安"}

SILENT_COMPANY_KEYWORDS = {
    "陪我", "陪着我", "别问了", "不想聊", "不想说", "安静待会", "静静待着",
    "挂着", "你在就行", "陪我待会", "陪一下", "不用说太多",
}

CLARIFY_HINT_KEYWORDS = {
    "说不清", "很乱", "纠结", "矛盾", "卡住", "想不明白", "理不清",
}

NEGATIVE_EXPRESSION_KEYWORDS = LOW_MOOD_KEYWORDS | {"烦", "委屈", "压抑", "难过", "心累"}


def _contains_any(text: str, keywords: set[str]) -> bool:
    return any(word in text for word in keywords)


def _is_explicit_advice_request(query: str) -> bool:
    q = query or ""
    return any(k in q for k in ("怎么做", "怎么办", "方法", "步骤", "建议", "解决"))


def _is_silent_company_request(query: str) -> bool:
    return _contains_any(query or "", SILENT_COMPANY_KEYWORDS)


def _has_negative_expression(query: str) -> bool:
    return _contains_any(query or "", NEGATIVE_EXPRESSION_KEYWORDS)


def _needs_clarify_support(query: str) -> bool:
    text = query or ""
    if _contains_any(text, CLARIFY_HINT_KEYWORDS):
        return True
    if text.count("不知道") >= 2:
        return True
    return text.count("？") + text.count("?") >= 2


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


def _build_companionship_need(
    query: str,
    risk_level: str,
    explicit_advice_request: bool,
    pending_confirmation: str = "",
    user_confirmed: bool = False,
) -> Dict[str, Any]:
    text = query or ""
    if risk_level == "high_risk":
        return {
            "mode": "risk_support",
            "reason": "risk_keyword_detected",
            "confidence": 0.96,
            "need_confirmation": False,
        }
    if pending_confirmation == "permission_advice" and user_confirmed:
        return {
            "mode": "advice",
            "reason": "confirmed_permission_advice",
            "confidence": 0.92,
            "need_confirmation": False,
        }
    if explicit_advice_request and _contains_any(text, ADVISE_HINT_KEYWORDS):
        return {
            "mode": "advice",
            "reason": "explicit_help_request",
            "confidence": 0.90,
            "need_confirmation": False,
        }
    if _contains_any(text, GUIDE_HINT_KEYWORDS):
        return {
            "mode": "guide",
            "reason": "actionable_help_request",
            "confidence": 0.84,
            "need_confirmation": False,
        }
    if _is_silent_company_request(text):
        return {
            "mode": "silent_company",
            "reason": "low_disclosure",
            "confidence": 0.88,
            "need_confirmation": False,
        }
    if _needs_clarify_support(text):
        return {
            "mode": "clarify",
            "reason": "mixed_or_confused_expression",
            "confidence": 0.76,
            "need_confirmation": True,
        }
    if risk_level == "low_mood" or _has_negative_expression(text):
        return {
            "mode": "reflect",
            "reason": "negative_expression_no_help_request",
            "confidence": 0.78,
            "need_confirmation": True,
        }
    return {
        "mode": "reflect",
        "reason": "default_supportive_presence",
        "confidence": 0.55,
        "need_confirmation": True,
    }


def _companionship_mode_to_intent(mode: str, query: str, risk_level: str) -> str:
    if mode == "risk_support":
        return "reflect"
    if mode == "advice":
        return "advise"
    if mode == "guide":
        return "guide"
    if mode == "clarify":
        return "clarify"
    if mode == "silent_company":
        if _contains_any((query or "").lower(), SHIFT_HINT_KEYWORDS):
            return "shift"
        return "reflect"
    if risk_level == "low_mood":
        return "reflect"
    return "reflect"


def _reconcile_intent(
    intent: str,
    query: str,
    risk_level: str,
    companionship_mode: str,
) -> str:
    """把关键词级意图接回主判定路径。

    历史问题：`_select_intent()` 定义了却没被任何地方调用（死代码），
    于是 "换个话题吧""算了先不说了" 这类明确转移请求全都落到 reflect，
    注入了"先接住情绪、不追问原因"这类完全错配的约束。
    这里只做保守覆盖：advice/guide 优先，其余场景允许关键词改判。
    """
    if risk_level == "high_risk":
        return "reflect"
    if companionship_mode in ("advice", "guide"):
        return intent
    keyword_intent = _select_intent(query, risk_level)
    if keyword_intent == "shift":
        return "shift"
    return intent


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


def _style_for_companionship_need(
    need: Dict[str, Any], base_style: Dict[str, str | int], risk_level: str
) -> Dict[str, str | int]:
    style = dict(base_style)
    mode = str(need.get("mode") or "")
    if mode == "silent_company":
        style.update(
            {
                "tone": "gentle",
                "length": "short",
                "question_density": "low",
                "initiative": "low",
                "suggestion_strength": 0,
            }
        )
    elif mode == "reflect":
        style.update(
            {
                "tone": "gentle",
                "length": "short",
                "question_density": "low",
                "initiative": "low",
                "suggestion_strength": 0 if risk_level == "low_mood" else 1,
            }
        )
    elif mode == "clarify":
        style.update(
            {
                "tone": "gentle",
                "length": "short",
                "question_density": "high",
                "initiative": "low",
                "suggestion_strength": 0,
            }
        )
    elif mode == "guide":
        style.update(
            {
                "tone": "neutral",
                "length": "medium",
                "question_density": "low",
                "initiative": "high",
                "suggestion_strength": 1,
            }
        )
    elif mode == "advice":
        style.update(
            {
                "tone": "neutral",
                "length": "medium",
                "question_density": "low",
                "initiative": "high",
                "suggestion_strength": 2,
            }
        )
    elif mode == "risk_support":
        style.update(
            {
                "tone": "gentle",
                "length": "short",
                "question_density": "low",
                "initiative": "low",
                "suggestion_strength": 0,
            }
        )
    return style


def _apply_entry_context_bias(
    companionship_need: Dict[str, Any],
    style: Dict[str, str | int],
    query: str,
    risk_level: str,
    entry_context: Dict[str, Any] | None,
) -> tuple[Dict[str, Any], Dict[str, str | int]]:
    context = entry_context or {}
    if not context.get("immersive_mode"):
        return companionship_need, style

    biased_need = dict(companionship_need)
    biased_style = dict(style)
    mode = str(biased_need.get("mode") or "")
    scene = str(context.get("entry_scene") or "").lower()
    query_text = (query or "").strip()

    biased_style.update(
        {
            "tone": "gentle",
            "length": "short",
            "question_density": "low",
            "initiative": "low",
            "suggestion_strength": 0 if risk_level in ("low_mood", "high_risk") else 1,
        }
    )

    if (
        scene == "comfort"
        and not query_text
        and mode not in {"risk_support", "advice", "guide"}
    ):
        biased_need = {
            "mode": "silent_company",
            "reason": "immersive_comfort_entry",
            "confidence": 0.82,
            "need_confirmation": False,
        }
    elif (
        scene == "comfort"
        and mode == "reflect"
        and not _is_explicit_advice_request(query)
        and not _contains_any(query_text, GUIDE_HINT_KEYWORDS)
        and risk_level != "high_risk"
    ):
        biased_need = dict(biased_need)
        biased_need["reason"] = "immersive_comfort_reflect"

    return biased_need, biased_style


def _select_micro_skill(companionship_mode: str, risk_level: str) -> str:
    # 高风险场景不给任何"模板句"：软化式复述（"你可能更像在低落，我理解得对吗"）
    # 会稀释风险表达，也不该用反问把确认责任推回给用户。
    # 高风险的安全响应由 HIGH_RISK_GUIDANCE 约束块单独承担。
    if risk_level == "high_risk":
        return "none"
    if companionship_mode == "silent_company":
        return "silent_company_opening"
    if companionship_mode == "reflect":
        return "reflect_label"
    if companionship_mode == "clarify":
        return "choice_question"
    if companionship_mode == "guide":
        return "guide_step"
    if companionship_mode == "advice":
        return "permission_advice"
    if companionship_mode == "summarize_followup":
        return "soft_wrapup"
    return "none"


def _select_micro_skill_with_state(
    companionship_mode: str,
    risk_level: str,
    explicit_advice_request: bool,
    policy_state: Dict[str, Any] | None,
) -> str:
    state = policy_state or {}
    permission_cooldown = int(state.get("permission_advice_cooldown_turns") or 0)
    blocked_micro_skill = str(state.get("blocked_micro_skill") or "")
    blocked_micro_skill_turns = int(state.get("blocked_micro_skill_turns") or 0)
    if companionship_mode == "advice":
        if explicit_advice_request or permission_cooldown > 0:
            return "none"
    selected = _select_micro_skill(companionship_mode, risk_level)
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
    # 旧实现：label 为空或"中性"时兜底成"有点累" —— 这是"听起来你有点有点累。"的根因。
    # 现在：识别不到情绪就不给词，由 MICRO_SKILL_REQUIRED_SLOTS 拦住不渲染。
    if label == "中性":
        label = ""
    emotion_phrase = EMOTION_PHRASES.get(label, "")
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

    guide_step = "先把现在最难受的点写成一句话，再决定下一步"
    if "失恋" in query or "被甩" in query:
        guide_step = "先去喝点水、换个位置坐下，让身体先缓一缓"
    elif "焦虑" in query or "紧张" in query:
        guide_step = "先做 3 次慢呼吸，把注意力拉回到当下"
    elif explicit_advice_request:
        guide_step = "先做 1 个最省力、今天就能完成的小动作"

    return {
        "emotion_phrase": emotion_phrase,
        "state": label,
        "option_a": option_a,
        "option_b": option_b,
        "follow_topic": follow_topic,
        "guide_step": guide_step,
    }


def _render_micro_skill_text(
    micro_skill_id: str,
    query: str,
    intent: str,
    risk_level: str,
    hypothesis: Dict[str, Any],
    explicit_advice_request: bool,
) -> str:
    if micro_skill_id == "silent_company_opening":
        return MICRO_SKILLS.get(micro_skill_id, "")
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
    required = MICRO_SKILL_REQUIRED_SLOTS.get(micro_skill_id, ())
    if any(not str(slots.get(key) or "").strip() for key in required):
        # 关键槽位缺失时宁可不给句子，也不要编一句和用户无关的话塞进 prompt
        return ""
    try:
        return template.format(**slots)
    except Exception:
        return ""


def _build_emotion_hypothesis(query: str, risk_level: str) -> Dict[str, Any]:
    text = query or ""
    if risk_level == "high_risk":
        return {
            "label": "很难受",
            "polarity": "negative",
            "confidence": 0.9,
            "source": "text",
        }
    for label, cues in NEGATIVE_EMOTION_CUES:
        if any(cue in text for cue in cues):
            return {
                "label": label,
                "polarity": "negative",
                "confidence": 0.68,
                "source": "text",
            }
    if any(cue in text for cue in POSITIVE_EMOTION_CUES):
        return {
            "label": "正面",
            "polarity": "positive",
            "confidence": 0.7,
            "source": "text",
        }
    # 识别不到就如实说识别不到，不再伪造"有点累"
    return {
        "label": "",
        "polarity": "unknown",
        "confidence": 0.0,
        "source": "unknown",
    }


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
    entry_context: Dict[str, Any] | None = None,
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
    explicit_advice_request = _is_explicit_advice_request(query)
    companionship_need = _build_companionship_need(
        query=query,
        risk_level=risk_level,
        explicit_advice_request=explicit_advice_request,
        pending_confirmation=pending_confirmation,
        user_confirmed=bool(user_confirmed),
    )
    intent = _companionship_mode_to_intent(
        companionship_need.get("mode", ""), query=query, risk_level=risk_level
    )
    intent = _apply_intent_rhythm(intent, last_intent=last_intent, query=query)
    if pending_confirmation == "permission_advice" and user_confirmed:
        intent = "advise"
        companionship_need = {
            "mode": "advice",
            "reason": "confirmed_permission_advice",
            "confidence": 0.92,
            "need_confirmation": False,
        }
    style = _style_for_companionship_need(
        companionship_need, _select_style(intent, risk_level), risk_level
    )
    companionship_need, style = _apply_entry_context_bias(
        companionship_need,
        style,
        query=query,
        risk_level=risk_level,
        entry_context=entry_context,
    )
    intent = _companionship_mode_to_intent(
        companionship_need.get("mode", ""), query=query, risk_level=risk_level
    )
    intent = _reconcile_intent(
        intent,
        query=query,
        risk_level=risk_level,
        companionship_mode=str(companionship_need.get("mode") or ""),
    )
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

    wants_checklist_output = _wants_checklist_output(query)
    effective_state = {
        "permission_advice_cooldown_turns": permission_cooldown,
        "music_confirmation_cooldown_turns": music_cooldown,
        "blocked_micro_skill": blocked_micro_skill,
        "blocked_micro_skill_turns": blocked_micro_skill_turns,
    }
    micro_skill_id = (
        _select_micro_skill_with_state(
            companionship_mode=str(companionship_need.get("mode") or ""),
            risk_level=risk_level,
            explicit_advice_request=explicit_advice_request,
            policy_state=effective_state,
        )
        if enable_micro_skills
        else "none"
    )
    if micro_skill_id == "reflect_label" and emotion_hypothesis.get("polarity") != "negative":
        # 共情标注只在识别到明确负面情绪时才有意义。
        # 用户说"今天考得还行"却被告知"你有点累"，比不给约束更糟。
        micro_skill_id = "none"
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

    need_emotion_confirmation = bool(companionship_need.get("need_confirmation")) and (
        companionship_need.get("mode") in ("reflect", "clarify")
        or emotion_hypothesis["confidence"] < 0.8
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
        "companionship_need": companionship_need,
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
        "entry_context": entry_context or {},
    }


def render_policy_constraints(policy: Dict[str, Any]) -> str:
    """把结构化决策编译成提示词约束块。

    设计原则（2026-11-02 修订）：只保留能改变生成的字段。
    旧版把"陪伴模式原因 / 置信度 / 入口上下文 {} / 弱证据 dict 原文 /
    当前是否要求分点输出: False / 确认后续动作: 按当前策略继续"等诊断回显
    一并塞进 prompt，共 800+ 字符，其中真正起作用的不足 5 行，
    对小模型构成指令稀释，实测自然度与连贯度反而下降。
    """
    risk_level = str(policy.get("risk_level") or "normal")
    blocks: List[str] = []

    # ① 本轮回应要求
    head = [
        "[本轮回应要求]",
        f"- 意图: {policy.get('intent_label')}",
        (
            f"- 语气: {policy.get('tone')}｜长度: {policy.get('length')}"
            f"｜提问密度: {policy.get('question_density')}"
        ),
        (
            f"- 主动性: {policy.get('initiative')}"
            f"｜建议强度: {policy.get('suggestion_strength')}"
        ),
    ]
    music_role = str(policy.get("music_role") or "none")
    if music_role != "none":
        head.append(f"- 音乐使用方式: {music_role}")
    micro_id = str(policy.get("micro_skill_id") or "none")
    micro_text = str(policy.get("micro_skill_text") or "").strip()
    if micro_id != "none":
        head.append(f"- 本轮微技能: {micro_id}")
        if micro_text:
            head.append(f"- 参考句式（须用自己的口语改写，禁止原句照抄）: {micro_text}")
    if policy.get("need_confirmation"):
        head.append("- 本轮需要先做软确认，确认后再推进。")
    blocks.append("\n".join(head))

    # ② 弱证据：只在真有信号时出现，且用自然语言而非 dict 原文
    weak: List[str] = []
    music_hyp = policy.get("music_hypothesis") or {}
    if isinstance(music_hyp, dict) and str(music_hyp.get("label") or "none") != "none":
        weak.append(
            f"- 音乐:{music_hyp.get('label')}（置信度 {music_hyp.get('confidence')}，仅弱证据）"
        )
    emo = policy.get("emotion_hypothesis") or {}
    if isinstance(emo, dict) and str(emo.get("label") or ""):
        weak.append(
            f"- 情绪:{emo.get('label')}（置信度 {emo.get('confidence')}）"
            f"——只可用于软确认，不得表述成结论"
        )
    if weak:
        blocks.append("[可用弱证据]\n" + "\n".join(weak))

    # ③ 记忆精注入
    if policy.get("memory_to_inject"):
        rows = []
        for item in policy["memory_to_inject"]:
            if isinstance(item, dict):
                rows.append(f"- ({item.get('source', 'unknown')}) {item.get('summary', '')}")
            else:
                rows.append(f"- {item}")
        blocks.append("[记忆精注入(仅本轮)]\n" + "\n".join(rows))

    # ④ 边界策略（高风险走独立约束块）
    if risk_level == "high_risk":
        blocks.append(HIGH_RISK_GUIDANCE)
    else:
        blocks.append(
            "[边界策略]\n"
            "- 不做心理诊断，不贴人格标签。\n"
            "- 不替代医疗/心理/法律等专业帮助。\n"
            "- 音乐与画像只当弱证据，必须可被用户纠正。\n"
            "- 如用户低落，先陪伴后建议。"
        )

    # ⑤ 生成约束（按本轮策略裁剪，不再无条件全量输出）
    gen = ["[生成约束]", "- 自然口语，禁止重复句和口头禅堆叠。"]
    if policy.get("suggestion_strength") not in (0, None):
        gen.append("- 建议点最多 2~3 个，不列长清单。")
    else:
        gen.append("- 本轮不给具体建议，先接住情绪。")
    if policy.get("explicit_advice_request"):
        gen.append("- 用户已明确要方法，直接给可执行步骤，不要再问是否需要建议。")
    if policy.get("wants_checklist_output"):
        gen.append("- 用户要求分点，可用编号列表。")
    else:
        gen.append("- 不要输出编号列表。")
    blocks.append("\n".join(gen))

    return "\n\n".join(blocks)
