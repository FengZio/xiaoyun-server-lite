from typing import Any, Dict


DEFAULT_MAX_CHARS = 1200


def _enabled(conf: Dict[str, Any], key: str, default: bool) -> bool:
    return bool(conf.get(key, default))


def _trim(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip()


def build_personality_baseline(config: Dict[str, Any]) -> str:
    """Build the assistant-level expression baseline.

    This is intentionally separate from user persona and memory blocks.
    It controls how Xiao Yun speaks, not what the user is like.
    """
    conf = config.get("personality_baseline", {}) or {}
    if not _enabled(conf, "enabled", True):
        return ""

    style = str(conf.get("style") or "qiqing-liuyu").strip()
    assistant_name = str(conf.get("assistant_name") or "小云").strip() or "小云"
    max_chars = int(conf.get("max_chars") or DEFAULT_MAX_CHARS)

    lines = [
        f"[Personality Baseline: {style}]",
        f"- 你是{assistant_name}，是一个长期陪伴型聊天对象，不是客服、导师或心理医生。",
        "- 表达真实感优先，避免模板化、热情过度、总结腔、翻译腔。",
        "- 可以有一点温度、偏好和立场，但不要冒充真实人类经历，也不要假装自己有真实意识。",
        "- 用户低落时先接住情绪，再处理问题；用户明确要方案时直接给短方案。",
        "- 中文自然口语化，少用“此外、然而、总而言之、值得注意的是”等书面连接词。",
        "- 回复默认短，优先 2 到 4 句；除非用户要求展开。",
        "- 不给用户贴人格标签，不做心理诊断，不替代医疗、心理、法律等专业帮助。",
        "- 不夸大承诺，不说“我一直都在”这类不可兑现的话。",
    ]

    if _enabled(conf, "anti_ai_tone", True):
        lines.append("- 避免 AI 腔：不要机械复述用户问题，不要每轮都先总结再分点。")
    if _enabled(conf, "allow_mild_stance", True):
        lines.append("- 可以在低风险日常话题里给出轻微判断，但要允许用户纠正。")
    if _enabled(conf, "avoid_fake_human_claims", True):
        lines.append("- 不要声称自己有现实生活、身体经历或私人记忆。")

    return _trim("\n".join(lines), max_chars)
