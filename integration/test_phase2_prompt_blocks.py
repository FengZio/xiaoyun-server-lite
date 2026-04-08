import os
import sys

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

try:
    from core.utils.prompt_blocks import PromptBlocks, PromptBlock, PromptAssembler
    from core.utils.memory_packer import pack
except ModuleNotFoundError:
    import importlib.util

    def _load_module(name: str, rel_path: str):
        path = os.path.join(PROJECT_ROOT, rel_path)
        if not os.path.exists(path):
            raise ModuleNotFoundError(f"Missing module file: {path}")
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)  # type: ignore
        return module

    prompt_blocks = _load_module("prompt_blocks", "core/utils/prompt_blocks.py")
    memory_packer = _load_module("memory_packer", "core/utils/memory_packer.py")

    PromptBlocks = prompt_blocks.PromptBlocks
    PromptBlock = prompt_blocks.PromptBlock
    PromptAssembler = prompt_blocks.PromptAssembler
    pack = memory_packer.pack


def test_insert_when_no_tags():
    base = (
        "[角色设定]\n你是{{assistant_name}}\n"
        "[核心特征]\n- A\n"
        "[交互指南]\n- B\n"
    )
    blocks = PromptBlocks(
        policy_block=PromptBlock(text="POLICY", usage_hint="必须遵守以下策略。"),
        persona_block=PromptBlock(text="PERSONA", usage_hint="用于风格，但不得编造事实。"),
        state_block=PromptBlock(text="STATE", usage_hint="优先处理待确认事项与未闭环。"),
        memory_block=PromptBlock(text="MEMORY", usage_hint="可引用事实，冲突需澄清。"),
    )
    rendered, stats = PromptAssembler.render(base, blocks)
    assert "<policy>" in rendered and "</policy>" in rendered
    assert "<persona>" in rendered and "</persona>" in rendered
    assert "<state>" in rendered and "</state>" in rendered
    assert "<memory>" in rendered and "</memory>" in rendered
    assert stats["policy"] in ("inserted", "appended", "replaced")


def test_replace_when_tags_exist():
    base = (
        "[交互指南]\n- B\n"
        "<policy>OLD</policy>\n"
        "<persona>OLD</persona>\n"
        "<state>OLD</state>\n"
        "<memory>OLD</memory>\n"
    )
    blocks = PromptBlocks(
        policy_block=PromptBlock(text="POLICY"),
        persona_block=PromptBlock(text="PERSONA"),
        state_block=PromptBlock(text="STATE"),
        memory_block=PromptBlock(text="MEMORY"),
    )
    rendered, stats = PromptAssembler.render(base, blocks)
    assert "<policy>\nPOLICY\n</policy>" in rendered
    assert "<persona>\nPERSONA\n</persona>" in rendered
    assert "<state>\nSTATE\n</state>" in rendered
    assert "<memory>\nMEMORY\n</memory>" in rendered
    assert stats["policy"] == "replaced"
    assert stats["persona"] == "replaced"
    assert stats["state"] == "replaced"
    assert stats["memory"] == "replaced"


def test_memory_packer_drops_current():
    items = [
        {"source": "stable", "type": "preference", "text": "偏好: 简短"},
        {"source": "episodic", "type": "recent", "text": "没有搭子没有计划"},
        {"source": "long_term", "type": "summary", "text": "以前也说过没有搭子没有计划"},
    ]
    text, stats = pack(items, current_user_text="没有搭子没有计划", max_items=3)
    assert "- (episodic) 没有搭子没有计划" not in text
    assert stats["dropped_current"] == 1


if __name__ == "__main__":
    test_insert_when_no_tags()
    test_replace_when_tags_exist()
    test_memory_packer_drops_current()
    print("Phase2 tests passed.")
