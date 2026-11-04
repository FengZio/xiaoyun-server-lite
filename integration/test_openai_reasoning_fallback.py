import os
import sys
from types import SimpleNamespace
import types

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

logger_stub = SimpleNamespace(
    bind=lambda **kwargs: SimpleNamespace(
        info=lambda *args, **kwargs: None,
        warning=lambda *args, **kwargs: None,
        error=lambda *args, **kwargs: None,
    )
)
sys.modules.setdefault("loguru", types.SimpleNamespace(logger=logger_stub))
sys.modules.setdefault(
    "config.logger",
    types.SimpleNamespace(setup_logging=lambda: logger_stub),
)

from core.providers.llm.openai.openai import LLMProvider


def _make_stream_chunk(content="", reasoning=""):
    delta = SimpleNamespace(
        content=content,
        reasoning_content=reasoning,
        output_text="",
        text="",
    )
    choice = SimpleNamespace(delta=delta)
    return SimpleNamespace(choices=[choice])


class _FakeChatCompletions:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("stream"):
            return [
                _make_stream_chunk(reasoning="先分析一下用户的表达"),
                _make_stream_chunk(reasoning="整理成可展示回复"),
            ]
        message = SimpleNamespace(content="这是回收出来的最终答案")
        choice = SimpleNamespace(message=message)
        return SimpleNamespace(choices=[choice])


class _FakeClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=_FakeChatCompletions())


def test_reasoning_only_stream_falls_back_to_non_stream_answer():
    provider = LLMProvider(
        {
            "api_key": "test-key",
            "model_name": "glm-4.7-flash",
            "base_url": "https://example.com/v1",
            "max_tokens": 128,
        }
    )
    provider.client = _FakeClient()

    result = "".join(
        provider.response(
            "session-1",
            [{"role": "user", "content": "我今天有点难受"}],
        )
    )

    assert result == "这是回收出来的最终答案"
    calls = provider.client.chat.completions.calls
    assert len(calls) == 2
    assert calls[0]["stream"] is True
    assert calls[1]["stream"] is False
