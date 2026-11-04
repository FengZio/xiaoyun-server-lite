import os
import sys

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.utils.dialogue import Dialogue, Message
from core.utils.dialogue_policy import build_dialogue_policy, render_policy_constraints


def _build_policy(query: str, music_signals=None):
    dialogue = Dialogue()
    dialogue.put(Message(role="user", content="最近有点乱"))
    dialogue.put(Message(role="assistant", content="我在，慢慢说。"))
    dialogue.put(Message(role="user", content=query))
    return build_dialogue_policy(
        query=query,
        dialogue=dialogue.dialogue,
        memory_str="",
        config={"dialogue_policy": {"enabled": True, "max_memory_items": 3}},
        current_user_text=query,
        music_signals=music_signals or {},
    )


def _build_immersive_policy(query: str):
    dialogue = Dialogue()
    dialogue.put(Message(role="user", content="我在。"))
    return build_dialogue_policy(
        query=query,
        dialogue=dialogue.dialogue,
        memory_str="",
        config={"dialogue_policy": {"enabled": True, "max_memory_items": 3}},
        current_user_text=query,
        music_signals={"low_arousal_ratio": 0.82, "recent_play_count": 3, "source": "test"},
        entry_context={
            "immersive_mode": True,
            "entry_source": "manual_immersive",
            "entry_scene": "comfort",
            "input_preference": "voice_first",
        },
    )


def test_negative_expression_prefers_reflect():
    policy = _build_policy("今天真的很烦，也有点累，不知道怎么扛。")
    assert policy["companionship_need"]["mode"] == "reflect"


def test_explicit_help_request_prefers_advice():
    policy = _build_policy("你直接告诉我该怎么做，给我步骤。")
    assert policy["companionship_need"]["mode"] in ("guide", "advice")


def test_guide_mode_uses_lightweight_step_skill():
    policy = _build_policy("帮我想个缓解办法，先给我一个能马上做的小步骤。")
    if policy["companionship_need"]["mode"] == "guide":
        assert policy["micro_skill_id"] == "guide_step"
        assert "先试一个很小的动作" in policy["micro_skill_text"]


def test_silent_company_request_detected():
    policy = _build_policy("先别问了，你就陪我待一会吧。")
    assert policy["companionship_need"]["mode"] == "silent_company"
    assert policy["micro_skill_id"] == "silent_company_opening"


def test_high_risk_routes_to_risk_support():
    policy = _build_policy("我有点不想活了。")
    assert policy["companionship_need"]["mode"] == "risk_support"
    assert policy["risk_level"] == "high_risk"


def test_music_signal_remains_weak_evidence_in_prompt():
    policy = _build_policy(
        "其实我今天还行，就是想随便聊聊。",
        music_signals={"low_arousal_ratio": 0.8, "recent_play_count": 5, "source": "test"},
    )
    rendered = render_policy_constraints(policy)
    assert policy["companionship_need"]["mode"] == "reflect"
    assert "[可用弱证据]" in rendered
    assert "仅弱证据" in rendered
    assert "不得表述成结论" in rendered


def test_immersive_comfort_prefers_low_pressure_modes():
    policy = _build_immersive_policy("我有点乱，但现在不太想被分析。")
    assert policy["companionship_need"]["mode"] in ("reflect", "silent_company")
    assert policy["tone"] == "gentle"
    assert policy["length"] == "short"


def test_immersive_prompt_keeps_non_diagnostic_boundary():
    policy = _build_immersive_policy("我今天其实还好。")
    rendered = render_policy_constraints(policy)
    assert "不做心理诊断" in rendered
    assert "仅弱证据" in rendered


# --- 2026-11-02 回归用例：对应"听起来你有点有点累"等三类缺陷 ---


def test_micro_skill_text_has_no_duplicated_degree_word():
    policy = _build_policy("今天好累啊，什么都不想干")
    assert policy["micro_skill_id"] == "reflect_label"
    text = policy["micro_skill_text"]
    assert text
    assert "有点有点" not in text
    assert "累" in text


def test_positive_expression_never_gets_negative_label():
    policy = _build_policy("今天考试考得还行，比上次好")
    assert policy["emotion_hypothesis"]["polarity"] == "positive"
    assert policy["micro_skill_id"] == "none"
    assert "累" not in policy["micro_skill_text"]


def test_unknown_emotion_does_not_inject_fabricated_phrase():
    policy = _build_policy("好，你说吧")
    assert policy["emotion_hypothesis"]["polarity"] == "unknown"
    assert policy["micro_skill_text"] == ""
    assert "有点累" not in render_policy_constraints(policy)


def test_shift_request_switches_intent():
    policy = _build_policy("换个话题吧，聊点轻松的")
    assert policy["intent"] == "shift"


def test_high_risk_uses_dedicated_guidance_not_soft_reframe():
    policy = _build_policy("真的不想活了")
    assert policy["risk_level"] == "high_risk"
    assert policy["micro_skill_id"] == "none"
    rendered = render_policy_constraints(policy)
    assert "[高风险响应要求]" in rendered
    assert "12356" in rendered
    # 旧的软化式复述句不得再出现（只允许作为禁止项被引用）
    assert "我感觉你可能更像在" not in rendered


def test_render_stays_compact_and_drops_diagnostic_echo():
    policy = _build_policy("今天好累啊，什么都不想干")
    rendered = render_policy_constraints(policy)
    assert len(rendered) < 600
    for noise in ("陪伴模式置信度", "入口上下文", "当前是否要求分点输出", "确认后续动作"):
        assert noise not in rendered
