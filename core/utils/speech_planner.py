import re
from typing import Dict, List, Optional


DEFAULT_STYLE = "自然"

STYLE_HINTS = [
    ("悲伤", ("难过", "伤心", "失落", "委屈", "哭", "遗憾", "没如果", "心酸", "崩溃")),
    ("生气", ("生气", "愤怒", "火大", "气死", "别乱", "烦死", "离谱", "这什么鬼", "机车")),
    ("悄悄话", ("悄悄", "小声", "轻声", "夜里", "夜深", "我在", "别怕", "慢慢来", "先别急")),
    ("开心", ("开心", "高兴", "兴奋", "太好了", "真棒", "笑死", "哈哈", "哈喽", "太赞了", "元气满满")),
]

TAG_RULES = [
    ("深呼吸", ("深呼吸", "冷静", "先稳住")),
    ("沉默片刻", ("如果我当时", "没如果", "是不是就不一样", "想不明白")),
    ("苦笑", ("没如果", "呵", "算了")),
    ("小声", ("我在", "别怕", "慢慢来")),
    ("提高音量喊话", ("大姐", "快走", "别乱翻")),
    ("长叹一口气", ("太累", "疲惫", "加班", "魂儿都要散了")),
    ("咳嗽", ("咳嗽", "冻透了", "呼——呼——")),
]


def _sanitize_style_token(token: str) -> str:
    token = re.sub(r"[<>\n\r]", " ", str(token or "")).strip()
    return token or DEFAULT_STYLE


def _select_style(text: str, agent_style: Optional[str] = None) -> str:
    script = (text or "").strip()
    for style, keywords in STYLE_HINTS:
        if any(keyword in script for keyword in keywords):
            return _sanitize_style_token(style)
    return DEFAULT_STYLE


def _inject_audio_tags(text: str) -> str:
    script = text.strip()
    if not script:
        return script

    for tag, keywords in TAG_RULES:
        if any(keyword in script for keyword in keywords):
            if script.startswith(f"（{tag}）"):
                break
            script = f"（{tag}）{script}"
            break

    script = re.sub(r"([。！？!?])", r"\1（停顿）", script, count=1)
    script = re.sub(r"（停顿）(?=（)", "", script)
    return script


def build_speech_plan(
    display_text: str,
    *,
    user_text: str = "",
    agent_style: Optional[str] = None,
) -> Dict[str, str]:
    """
    Build a MiMo-friendly speech script from display text.

    Returns:
        {
            "display_text": "...",
            "tts_style": "开心",
            "tts_text": "（小声）...",
            "assistant_text": "<style>开心</style>（小声）..."
        }
    """
    display_text = (display_text or "").strip()
    tts_style = _select_style(display_text, agent_style=agent_style)
    tts_text = _inject_audio_tags(display_text)
    assistant_text = f"<style>{tts_style}</style>{tts_text}" if tts_text else ""
    return {
        "display_text": display_text,
        "tts_style": tts_style,
        "tts_text": tts_text,
        "assistant_text": assistant_text,
    }
