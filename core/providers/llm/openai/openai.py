from typing import Any

import httpx
import openai
from openai.types import CompletionUsage

from config.logger import setup_logging
from core.providers.llm.base import LLMProviderBase
from core.utils.util import check_model_key

TAG = __name__
logger = setup_logging()


class LLMProvider(LLMProviderBase):
    def __init__(self, config):
        self.model_name = config.get("model_name")
        self.api_key = config.get("api_key")
        self.base_url = config.get("base_url") if "base_url" in config else config.get("url")

        timeout = config.get("timeout", 300)
        self.timeout = int(timeout) if timeout else 300

        param_defaults = {
            "max_tokens": (500, int),
            "temperature": (0.7, lambda x: round(float(x), 1)),
            "top_p": (1.0, lambda x: round(float(x), 1)),
            "frequency_penalty": (0, lambda x: round(float(x), 1)),
        }

        for param, (default, converter) in param_defaults.items():
            value = config.get(param)
            try:
                setattr(self, param, converter(value) if value not in (None, "") else default)
            except (ValueError, TypeError):
                setattr(self, param, default)

        model_key_msg = check_model_key("LLM", self.api_key)
        if model_key_msg:
            logger.bind(tag=TAG).error(model_key_msg)

        self.client = openai.OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=httpx.Timeout(self.timeout),
        )

    @staticmethod
    def _extract_text_parts(content: Any) -> str:
        if not content:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            text_parts = []
            for part in content:
                if isinstance(part, str):
                    text_parts.append(part)
                elif isinstance(part, dict) and part.get("type") == "text":
                    text_parts.append(part.get("text", ""))
                else:
                    text = getattr(part, "text", "")
                    if text:
                        text_parts.append(text)
            return "".join(text_parts)
        text = getattr(content, "text", "")
        return text if isinstance(text, str) else ""

    @classmethod
    def _extract_stream_content(cls, delta: Any) -> str:
        if delta is None:
            return ""
        candidates = [
            getattr(delta, "content", ""),
            getattr(delta, "output_text", ""),
            getattr(delta, "text", ""),
        ]
        for candidate in candidates:
            text = cls._extract_text_parts(candidate)
            if text:
                return text
        return ""

    @classmethod
    def _extract_reasoning_content(cls, delta: Any) -> str:
        if delta is None:
            return ""
        candidates = [
            getattr(delta, "reasoning_content", ""),
            getattr(delta, "reasoning", ""),
        ]
        for candidate in candidates:
            text = cls._extract_text_parts(candidate)
            if text:
                return text
        return ""

    @classmethod
    def _extract_message_content(cls, message: Any) -> str:
        if message is None:
            return ""
        candidates = [
            getattr(message, "content", ""),
            getattr(message, "output_text", ""),
            getattr(message, "text", ""),
        ]
        for candidate in candidates:
            text = cls._extract_text_parts(candidate)
            if text:
                return text
        return ""

    def _fallback_non_stream_response(self, dialogue, **kwargs) -> str:
        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=dialogue,
                stream=False,
                max_tokens=kwargs.get("max_tokens", self.max_tokens),
                temperature=kwargs.get("temperature", self.temperature),
                top_p=kwargs.get("top_p", self.top_p),
                frequency_penalty=kwargs.get("frequency_penalty", self.frequency_penalty),
            )
            choices = getattr(response, "choices", None) or []
            if not choices:
                return ""
            message = getattr(choices[0], "message", None)
            content = self._extract_message_content(message)
            if content:
                logger.bind(tag=TAG).info(
                    f"Recovered final answer via non-stream fallback. model={self.model_name}"
                )
            return content
        except Exception as e:
            logger.bind(tag=TAG).warning(
                f"Non-stream fallback failed for model={self.model_name}: {e}"
            )
            return ""

    def response(self, session_id, dialogue, **kwargs):
        try:
            total_chunks = 0
            emitted_chunks = 0
            reasoning_only_chunks = 0
            reasoning_preview = ""

            responses = self.client.chat.completions.create(
                model=self.model_name,
                messages=dialogue,
                stream=True,
                max_tokens=kwargs.get("max_tokens", self.max_tokens),
                temperature=kwargs.get("temperature", self.temperature),
                top_p=kwargs.get("top_p", self.top_p),
                frequency_penalty=kwargs.get("frequency_penalty", self.frequency_penalty),
            )

            is_active = True
            for chunk in responses:
                total_chunks += 1

                try:
                    delta = chunk.choices[0].delta if getattr(chunk, "choices", None) else None
                    content = self._extract_stream_content(delta)
                    reasoning_content = self._extract_reasoning_content(delta)
                except IndexError:
                    content = ""
                    reasoning_content = ""

                if not content and reasoning_content:
                    reasoning_only_chunks += 1
                    if len(reasoning_preview) < 200:
                        reasoning_preview += reasoning_content

                if content:
                    if "<think>" in content:
                        is_active = False
                        content = content.split("<think>")[0]
                    if "</think>" in content:
                        is_active = True
                        content = content.split("</think>")[-1]

                    if is_active:
                        emitted_chunks += 1
                        yield content

            if total_chunks > 0 and emitted_chunks == 0:
                logger.bind(tag=TAG).warning(
                    "LLM stream finished with no content chunks. "
                    f"model={self.model_name}, total_chunks={total_chunks}, "
                    f"reasoning_only_chunks={reasoning_only_chunks}, "
                    f"reasoning_preview={reasoning_preview[:120]!r}"
                )
                fallback_content = self._fallback_non_stream_response(dialogue, **kwargs)
                if fallback_content:
                    yield fallback_content
                    return
                if reasoning_only_chunks > 0:
                    yield "我刚刚卡住了一下。你再说一句，或者我也可以直接接着刚才的话继续。"
                    return

        except Exception as e:
            logger.bind(tag=TAG).error(f"Error in response generation: {e}")

    def response_with_functions(self, session_id, dialogue, functions=None):
        try:
            stream = self.client.chat.completions.create(
                model=self.model_name,
                messages=dialogue,
                stream=True,
                tools=functions,
            )

            for chunk in stream:
                if getattr(chunk, "choices", None):
                    yield chunk.choices[0].delta.content, chunk.choices[0].delta.tool_calls
                elif isinstance(getattr(chunk, "usage", None), CompletionUsage):
                    usage_info = getattr(chunk, "usage", None)
                    logger.bind(tag=TAG).info(
                        "Token usage: "
                        f"prompt={getattr(usage_info, 'prompt_tokens', 'unknown')}, "
                        f"completion={getattr(usage_info, 'completion_tokens', 'unknown')}, "
                        f"total={getattr(usage_info, 'total_tokens', 'unknown')}"
                    )

        except Exception as e:
            logger.bind(tag=TAG).error(f"Error in function call streaming: {e}")
            yield f"OpenAI service response error: {e}", None
