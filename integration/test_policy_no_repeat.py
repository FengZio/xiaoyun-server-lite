import os
import sys

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.utils.dialogue import Dialogue, Message
from core.utils.dialogue_policy import build_dialogue_policy


def run_case():
    dialogue = Dialogue()
    # system prompt with <memory> and <policy> placeholders (optional)
    dialogue.put(
        Message(
            role="system",
            content=(
                "[角色设定]\n你是{{assistant_name}}\n"
                "<memory>\n{{memory}}\n</memory>\n"
                "<policy>\n{{policy}}\n</policy>\n"
            ),
        )
    )

    # Simulate a previous turn
    dialogue.put(Message(role="user", content="上周很无聊，待在家里"))
    dialogue.put(Message(role="assistant", content="那天你也太闲了吧"))

    # Current user input (the one we must NOT inject)
    current_query = "没有搭子没有计划"
    dialogue.put(Message(role="user", content=current_query))

    policy = build_dialogue_policy(
        query=current_query,
        dialogue=dialogue.dialogue,
        memory_str="",
        config={"dialogue_policy": {"enabled": True, "max_memory_items": 3}},
        current_user_text=current_query,
    )

    print("memory_to_inject_text:", policy.get("memory_to_inject_text"))
    assert all(
        current_query not in item for item in policy.get("memory_to_inject_text", [])
    ), "Current user input should not appear in memory injection"


if __name__ == "__main__":
    run_case()
