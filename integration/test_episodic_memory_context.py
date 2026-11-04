import os
import sys
import tempfile

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.utils.episodic_memory import EpisodicMemoryStore


def test_update_from_turn_persists_immersive_context():
    with tempfile.TemporaryDirectory() as tmp_dir:
        store = EpisodicMemoryStore(path=os.path.join(tmp_dir, "episodic.yaml"))
        changed = store.update_from_turn(
            "agent-1::user-1",
            "我想安静待一会。",
            "我在。",
            context={
                "immersive_mode": True,
                "entry_source": "manual_immersive",
                "entry_scene": "comfort",
                "companionship_mode": "silent_company",
                "silent_company": True,
            },
        )
        assert changed is True
        event = store._cache["agent-1::user-1"]["events"][0]
        assert event["context"]["immersive_mode"] is True
        assert event["context"]["entry_scene"] == "comfort"
        assert event["context"]["companionship_mode"] == "silent_company"
