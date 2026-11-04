import re
from typing import Dict, List, Optional


DEFAULT_STYLE = "自然"
DEFAULT_MIMO_STYLE = "温柔"
MIMO_TTS_PROMPT_RULES = (
    "你的回复会直接送入 mimo-v2-tts 语音合成。"
    "可以在回复开头少量使用 <style>温柔</style> 这类整体风格标签，"
    "也可以在句中克制使用（小声）（停顿片刻）（叹气）（语速放慢）等自然音频标签。"
    "标签必须服务于语气，不要每句都加，不要堆叠。"
    "不要输出 Markdown、JSON、代码块或解释性前缀。"
)

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


def extract_mimo_style(text: str) -> Optional[str]:
    match = re.search(r"<style>\s*(.*?)\s*</style>", str(text or ""), flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    return _sanitize_style_token(match.group(1))


def strip_mimo_tts_markup(text: str) -> str:
    display = str(text or "")
    display = re.sub(r"<style>.*?</style>", "", display, flags=re.IGNORECASE | re.DOTALL)
    display = re.sub(r"（[^（）]{1,30}）", "", display)
    display = re.sub(r"\([^()]{1,30}\)", "", display)
    display = re.sub(r"\s+", " ", display)
    display = re.sub(r"\s+([。！？!?；;，、：,.])", r"\1", display)
    return display.strip()


def ensure_mimo_style(text: str, style: Optional[str]) -> str:
    script = str(text or "").strip()
    if not script:
        return script
    if re.match(r"^\s*<style>.*?</style>", script, flags=re.IGNORECASE | re.DOTALL):
        return script
    if not style:
        return script
    return f"<style>{_sanitize_style_token(style)}</style>{script}"


def build_speech_plan(
    display_text: str,
    *,
    user_text: str = "",
    agent_style: Optional[str] = None,
    turn_style: Optional[str] = None,
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
    raw_text = (display_text or "").strip()
    display_text = strip_mimo_tts_markup(raw_text)
    tts_style = extract_mimo_style(raw_text) or turn_style or _select_style(display_text, agent_style=agent_style)
    assistant_text = ensure_mimo_style(raw_text, tts_style)
    return {
        "display_text": display_text,
        "tts_style": tts_style,
        "tts_text": assistant_text,
        "assistant_text": assistant_text,
    }


def build_mimo_prosody_system_prompt() -> str:
    return (
        "你是语音韵律生成器。"
        "请把输入文本改写为适合 mimo-v2-tts 直接合成的文本。"
        "允许的控制方式只有两种："
        "1. 开头使用 <style>风格</style>。"
        "2. 正文中使用自然音频标签，例如（小声）（停顿片刻）（叹气）（语速加快）（提高音量）。"
        "要求："
        "不要解释；不要输出 JSON；不要使用代码块；不要改变原文核心语义；"
        "标记必须克制，避免每句都加；输出必须是可直接送入 TTS 的纯文本。"
    )


def build_mimo_prosody_user_prompt(
    reply_text: str,
    *,
    user_text: str = "",
    speech_mode: str = "",
    risk_level: str = "",
    agent_style: Optional[str] = None,
) -> str:
    hints: List[str] = []
    if speech_mode:
        hints.append(f"当前陪伴模式：{speech_mode}")
    if risk_level:
        hints.append(f"风险等级：{risk_level}")
    if agent_style:
        hints.append(f"角色说话风格：{agent_style}")
    if user_text:
        hints.append(f"用户上一句：{user_text}")
    hints.append(
        "如果语气偏安抚，请优先使用 温柔/悄悄话/平静 等风格；"
        "如果是建议型回复，请保持自然、清晰，少用夸张标签。"
    )
    hints_text = "\n".join(hints)
    return f"{hints_text}\n\n输入文本：\n{reply_text}"


def extract_mimo_tts_text(raw_text: str, fallback_text: str, fallback_style: str = DEFAULT_MIMO_STYLE) -> str:
    text = (raw_text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip()
    text = re.sub(r"[\r\n]+", "\n", text).strip()
    if not text:
        return f"<style>{fallback_style}</style>{fallback_text}".strip()

    if not re.match(r"^\s*<style>.*?</style>", text):
        text = f"<style>{fallback_style}</style>{text}"

    # 防止模型输出解释性前缀。
    text = re.sub(r"^(好的|当然|以下是[^：:\n]*[:：])\s*", "", text).strip()
    if not text:
        return f"<style>{fallback_style}</style>{fallback_text}".strip()
    return text


def is_invalid_mimo_rewrite(raw_text: str, fallback_text: str) -> bool:
    text = (raw_text or "").strip()
    fallback = (fallback_text or "").strip()
    if not text:
        return True

    known_llm_fallbacks = (
        "我刚刚卡住了一下。你再说一句，或者我也可以直接接着刚才的话继续。",
    )
    if any(marker in text for marker in known_llm_fallbacks):
        return True

    if fallback:
        fallback_chars = {char for char in fallback if not char.isspace()}
        if fallback_chars:
            overlap = sum(1 for char in fallback_chars if char in text)
            if overlap / len(fallback_chars) < 0.15:
                return True

    return False
