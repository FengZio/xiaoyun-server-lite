import tempfile
from pathlib import Path
import sys

import yaml

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from core.utils.stable_memory import StableMemoryStore


def test_nickname_overwrite_persisted() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        memory_path = Path(tmpdir) / ".stable_memory.yaml"
        store = StableMemoryStore(path=str(memory_path))
        user_id = "overwrite_user_001"

        first_changed = store.update_from_user_text(user_id, "我叫小宇")
        second_changed = store.update_from_user_text(user_id, "我叫小张")

        assert first_changed is True
        assert second_changed is True
        assert store.get_profile(user_id)["nickname"] == "小张"

        disk_data = yaml.safe_load(memory_path.read_text(encoding="utf-8")) or {}
        assert disk_data[user_id]["nickname"] == "小张"


def test_agent_scoped_memory_isolated() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        memory_path = Path(tmpdir) / ".stable_memory.yaml"
        store = StableMemoryStore(path=str(memory_path))

        key_a = "agentA::same_user"
        key_b = "agentB::same_user"
        store.update_from_user_text(key_a, "我叫小宇")
        store.update_from_user_text(key_b, "我叫小张")

        assert store.get_profile(key_a)["nickname"] == "小宇"
        assert store.get_profile(key_b)["nickname"] == "小张"


def test_question_should_not_update_nickname() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        memory_path = Path(tmpdir) / ".stable_memory.yaml"
        store = StableMemoryStore(path=str(memory_path))
        user_id = "question_user_001"

        store.update_from_user_text(user_id, "我叫小宇")
        changed = store.update_from_user_text(user_id, "我叫什么还记得我吗")

        assert changed is False
        assert store.get_profile(user_id)["nickname"] == "小宇"


def test_tone_suffix_should_be_trimmed() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        memory_path = Path(tmpdir) / ".stable_memory.yaml"
        store = StableMemoryStore(path=str(memory_path))
        user_id = "suffix_user_001"

        changed = store.update_from_user_text(user_id, "我叫小张呢")

        assert changed is True
        assert store.get_profile(user_id)["nickname"] == "小张"


if __name__ == "__main__":
    test_nickname_overwrite_persisted()
    test_agent_scoped_memory_isolated()
    test_question_should_not_update_nickname()
    test_tone_suffix_should_be_trimmed()
    print("ok")
