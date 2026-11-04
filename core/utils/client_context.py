from typing import Any, Dict


SUPPORTED_UI_STATE_HINTS = {
    "idle_breathe",
    "listening",
    "soft_speaking",
    "silent_company",
}


def _safe_text(value: Any, limit: int = 64) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text[:limit]


def build_memory_scope_key(device_id: str | None, current_agent_id: str | None) -> str | None:
    memory_user_id = device_id
    if device_id and str(device_id).startswith("uniapp_"):
        memory_user_id = str(device_id)[7:]
    elif device_id and str(device_id).startswith("uniapp-"):
        memory_user_id = str(device_id)[7:]
    if not memory_user_id:
        return None
    agent_scope = _safe_text(current_agent_id, limit=80)
    return f"{agent_scope}::{memory_user_id}" if agent_scope else str(memory_user_id)


def normalize_entry_context(raw: Any, existing: Dict[str, Any] | None = None) -> Dict[str, Any]:
    merged = dict(existing or {})
    if isinstance(raw, dict):
        merged.update(raw)

    immersive_mode = bool(merged.get("immersive_mode"))
    entry_source = _safe_text(merged.get("entry_source"))
    entry_scene = _safe_text(merged.get("entry_scene")).lower()
    input_preference = _safe_text(merged.get("input_preference"))
    nfc_tag_id = _safe_text(merged.get("nfc_tag_id"), limit=96)
    nfc_scene = _safe_text(merged.get("nfc_scene")).lower()

    if immersive_mode and not entry_source:
        entry_source = "manual_immersive"
    if immersive_mode and not entry_scene:
        entry_scene = "comfort"
    if immersive_mode and not input_preference:
        input_preference = "voice_first"

    normalized: Dict[str, Any] = {}
    if immersive_mode:
        normalized["immersive_mode"] = True
    if entry_source:
        normalized["entry_source"] = entry_source
    if entry_scene:
        normalized["entry_scene"] = entry_scene
    if input_preference:
        normalized["input_preference"] = input_preference
    if nfc_tag_id:
        normalized["nfc_tag_id"] = nfc_tag_id
    if nfc_scene:
        normalized["nfc_scene"] = nfc_scene
    return normalized


def derive_ui_state_hint(companionship_need: Dict[str, Any] | None, phase: str = "policy") -> str:
    if phase == "response":
        return "soft_speaking"

    mode = str((companionship_need or {}).get("mode") or "").strip().lower()
    if mode == "silent_company":
        return "silent_company"
    if mode in {"reflect", "clarify", "guide", "advice", "risk_support"}:
        return "listening"
    return "idle_breathe"


def build_reply_meta(
    companionship_need: Dict[str, Any] | None,
    speech_plan: Dict[str, Any] | None = None,
    *,
    phase: str = "response",
) -> Dict[str, Any]:
    meta: Dict[str, Any] = {
        "companionship_need": companionship_need or {},
        "ui_state_hint": derive_ui_state_hint(companionship_need, phase=phase),
    }
    if speech_plan:
        meta["speech_plan"] = {
            "display_text": _safe_text(speech_plan.get("display_text"), limit=500),
            "tts_style": _safe_text(speech_plan.get("tts_style"), limit=32),
        }
    return meta


def build_episodic_turn_context(
    entry_context: Dict[str, Any] | None,
    companionship_need: Dict[str, Any] | None,
) -> Dict[str, Any]:
    context = normalize_entry_context(entry_context or {})
    result: Dict[str, Any] = {
        "immersive_mode": bool(context.get("immersive_mode")),
        "entry_source": context.get("entry_source", ""),
        "entry_scene": context.get("entry_scene", ""),
        "nfc_tag_id": context.get("nfc_tag_id", ""),
        "nfc_scene": context.get("nfc_scene", ""),
    }
    mode = str((companionship_need or {}).get("mode") or "")
    if mode:
        result["companionship_mode"] = mode
        result["silent_company"] = mode == "silent_company"
    return result
