from typing import Any, Dict, List


class StateManager:
    @staticmethod
    def export_for_prompt(policy_state: Dict[str, Any] | None, last_intent: str | None) -> str:
        state = policy_state or {}
        lines: List[str] = []
        pending = str(state.get("pending_confirmation") or "").strip()
        if pending:
            lines.append(f"待确认: {pending}")
        if last_intent:
            lines.append(f"上一轮意图: {last_intent}")
        open_loops = state.get("open_loops")
        if open_loops:
            if isinstance(open_loops, (list, tuple)):
                for item in open_loops:
                    if item:
                        lines.append(f"未闭环: {str(item)[:80]}")
            else:
                lines.append(f"未闭环: {str(open_loops)[:80]}")
        return "\n".join(lines).strip()
