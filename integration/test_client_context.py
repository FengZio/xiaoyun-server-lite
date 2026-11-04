import os
import sys

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.utils.client_context import (
    build_episodic_turn_context,
    build_memory_scope_key,
    build_reply_meta,
    normalize_entry_context,
)


def test_build_memory_scope_key_supports_uniapp_prefix_variants():
    assert build_memory_scope_key("uniapp_user-1", "agent-a") == "agent-a::user-1"
    assert build_memory_scope_key("uniapp-user-2", "agent-b") == "agent-b::user-2"


def test_normalize_entry_context_fills_immersive_defaults():
    context = normalize_entry_context({"immersive_mode": True})
    assert context["immersive_mode"] is True
    assert context["entry_source"] == "manual_immersive"
    assert context["entry_scene"] == "comfort"
    assert context["input_preference"] == "voice_first"


def test_build_reply_meta_exposes_ui_fields():
    meta = build_reply_meta(
        {"mode": "silent_company"},
        {"display_text": "我在。", "tts_style": "悄悄话"},
    )
    assert meta["companionship_need"]["mode"] == "silent_company"
    assert meta["ui_state_hint"] == "soft_speaking"
    assert meta["speech_plan"]["display_text"] == "我在。"


def test_build_episodic_turn_context_keeps_entry_metadata():
    context = build_episodic_turn_context(
        {
            "immersive_mode": True,
            "entry_source": "nfc_doll",
            "entry_scene": "comfort",
            "nfc_tag_id": "tag-001",
            "nfc_scene": "comfort",
        },
        {"mode": "silent_company"},
    )
    assert context["immersive_mode"] is True
    assert context["entry_source"] == "nfc_doll"
    assert context["entry_scene"] == "comfort"
    assert context["silent_company"] is True
