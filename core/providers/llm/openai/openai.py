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

    def response(self, session_id, dialogue, **kwargs):
        try:
            total_chunks = 0
            emitted_chunks = 0
            reasoning_only_chunks = 0

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
                    content = delta.content if hasattr(delta, "content") else ""
                    reasoning_content = delta.reasoning_content if hasattr(delta, "reasoning_content") else ""
                except IndexError:
                    content = ""
                    reasoning_content = ""

                if isinstance(content, list):
                    text_parts = []
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            text_parts.append(part.get("text", ""))
                    content = "".join(text_parts)

                if not content and reasoning_content:
                    reasoning_only_chunks += 1

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
                    f"reasoning_only_chunks={reasoning_only_chunks}"
                )

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
