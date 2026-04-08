import base64
import json
import os
from typing import Any, Dict, List, Optional

import requests

from core.providers.tts.base import TTSProviderBase


class TTSProvider(TTSProviderBase):
    def __init__(self, config, delete_audio_file):
        super().__init__(config, delete_audio_file)
        self.api_key = config.get("api_key", "")
        self.api_url = config.get("api_url", "https://api.xiaomimimo.com/v1")
        self.model = config.get("model", "mimo-v2-tts")
        self.voice = config.get("voice", "mimo_default")
        self.audio_file_type = config.get("format", "mp3")
        self.timeout = int(config.get("timeout", 60))
        self.extra_body = config.get("extra_body", {}) or {}

    async def text_to_speak(self, text, output_file):
        if not self.api_url:
            raise Exception("xiaomimimo TTS api_url 未配置")
        if not self.api_key:
            raise Exception("xiaomimimo TTS api_key 未配置")

        payload = self._build_payload(text)
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        response = requests.post(
            self._build_request_url(),
            json=payload,
            headers=headers,
            timeout=self.timeout,
        )
        response.raise_for_status()
        audio_bytes = self._extract_audio_bytes(response)
        if not audio_bytes:
            raise Exception("xiaomimimo TTS 未返回可用音频数据")

        if output_file:
            os.makedirs(os.path.dirname(output_file), exist_ok=True)
            with open(output_file, "wb") as file_to_save:
                file_to_save.write(audio_bytes)
            return None
        return audio_bytes

    def _build_payload(self, assistant_text: str) -> Dict[str, Any]:
        user_text = ""
        if getattr(self, "conn", None) is not None:
            user_text = getattr(self.conn, "last_user_message", "") or ""

        messages: List[Dict[str, str]] = []
        if user_text:
            messages.append({"role": "user", "content": user_text})
        messages.append({"role": "assistant", "content": assistant_text})

        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "audio": {
                "format": self.audio_file_type,
                "voice": self.voice,
            },
        }
        payload.update(self.extra_body)
        return payload

    def _build_request_url(self) -> str:
        base_url = self.api_url.rstrip("/")
        if base_url.endswith("/chat/completions"):
            return base_url
        return f"{base_url}/chat/completions"

    def _extract_audio_bytes(self, response: requests.Response) -> Optional[bytes]:
        content_type = (response.headers.get("Content-Type") or "").lower()
        if content_type.startswith("audio/"):
            return response.content

        data = response.json()
        audio_bytes = self._decode_openai_audio(data)
        if audio_bytes:
            return audio_bytes
        raise Exception(f"xiaomimimo TTS 响应格式不支持: {json.dumps(data, ensure_ascii=False)[:300]}")

    def _decode_openai_audio(self, data: Any) -> Optional[bytes]:
        if not isinstance(data, dict):
            return None

        choices = data.get("choices")
        if not isinstance(choices, list):
            return None

        for choice in choices:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message")
            if not isinstance(message, dict):
                continue
            audio = message.get("audio")
            if not isinstance(audio, dict):
                continue
            audio_data = audio.get("data")
            if not isinstance(audio_data, str):
                continue
            try:
                return base64.b64decode(audio_data)
            except Exception:
                return None
        return None
