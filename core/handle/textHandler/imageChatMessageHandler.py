import base64
import binascii
import json
import time
from typing import Any, Dict

import requests

from core.handle.textHandler.chatMessageHandler import ChatTextMessageHandler
from core.handle.textMessageType import TextMessageType
from core.utils import vllm as vllm_utils
from core.utils.util import (
    extract_json_from_string,
    get_vision_url,
    is_valid_image_file,
)

TAG = __name__


class ImageChatTextMessageHandler(ChatTextMessageHandler):
    """UniApp 图片聊天消息处理器"""

    MAX_IMAGE_BYTES = 8 * 1024 * 1024
    DEFAULT_PROMPT = "帮我看看这张图"

    @property
    def message_type(self) -> TextMessageType:
        return TextMessageType.IMAGE_CHAT

    async def handle(self, conn, msg_json: Dict[str, Any]) -> None:
        content = str(msg_json.get("content") or "").strip()
        enable_audio = msg_json.get("enable_audio", True)
        agent_id = msg_json.get("agent_id", None)
        image_base64 = str(msg_json.get("image_base64") or "").strip()
        image_mime = str(msg_json.get("image_mime") or "image/jpeg").strip()
        self._adopt_client_session_id(conn, msg_json)

        if not image_base64:
            await self._send_error_reply(conn, "图片数据不能为空")
            return

        try:
            image_bytes = self._decode_image_bytes(image_base64)
            self._validate_image(image_bytes, image_mime)
        except ValueError as exc:
            conn.logger.bind(tag=TAG).warning(f"图片消息校验失败: {exc}")
            await self._send_error_reply(conn, str(exc))
            return

        conn.last_activity_time = time.time() * 1000

        if agent_id:
            switch_success = await self._switch_agent_if_needed(conn, agent_id)
            if not switch_success:
                return

        conn.is_uniapp_client = True
        conn.enable_audio_response = enable_audio
        conn.uniapp_audio_files = []
        conn.last_user_message = content or self.DEFAULT_PROMPT
        self._ensure_report_device_id(conn, msg_json, agent_id)

        try:
            visual_context = self._build_visual_context(conn, image_base64, image_mime)
        except Exception as exc:
            conn.logger.bind(tag=TAG).warning(f"视觉识别失败，走降级回复: {exc}")
            visual_context = {
                "vision_summary": "图片内容暂时识别失败",
                "vision_entities": [],
                "vision_emotion_hint": "无可靠视觉线索",
                "source": "vision_fallback",
            }

        conn.pending_visual_context = visual_context
        conn.logger.bind(tag=TAG).info(
            f"图片聊天上下文已注入: source={visual_context.get('source')}, summary={visual_context.get('vision_summary', '')[:120]}"
        )

        from core.handle.reportHandle import enqueue_asr_report

        report_text = content or self.DEFAULT_PROMPT
        enqueue_asr_report(conn, report_text, [])

        await self._start_chat(conn, report_text)

    def _decode_image_bytes(self, image_base64: str) -> bytes:
        payload = image_base64
        if payload.startswith("data:"):
            _, _, payload = payload.partition(",")
        try:
            return base64.b64decode(payload, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError("图片数据格式错误，请重新选择图片")

    def _validate_image(self, image_bytes: bytes, image_mime: str) -> None:
        if not image_bytes:
            raise ValueError("图片数据为空，请重新选择图片")
        if len(image_bytes) > self.MAX_IMAGE_BYTES:
            raise ValueError("图片过大，请选择 8MB 以内的图片")
        if image_mime and not image_mime.lower().startswith("image/"):
            raise ValueError("仅支持图片格式")
        if not is_valid_image_file(image_bytes):
            raise ValueError("仅支持 JPEG/PNG/GIF/BMP/TIFF/WEBP 图片")

    def _build_visual_context(
        self, conn, image_base64: str, image_mime: str
    ) -> Dict[str, Any]:
        question = (
            "请识别这张图片，并只返回中文 JSON："
            '{"vision_summary":"一句话概述图片主要内容与场景","vision_entities":["最多3个主要元素"],'
            '"vision_emotion_hint":"仅作低置信度氛围线索，避免确定判断"}'
        )

        raw_response = self._call_visual_service(conn, question, image_base64, image_mime)
        parsed = self._parse_visual_response(raw_response)
        parsed["source"] = parsed.get("source") or "vision_model"
        return parsed

    def _call_visual_service(
        self, conn, question: str, image_base64: str, image_mime: str
    ) -> str:
        selected_module = conn.config.get("selected_module", {}) or {}
        selected_vllm = selected_module.get("VLLM")
        vllm_map = conn.config.get("VLLM", {}) or {}
        vllm_config = vllm_map.get(selected_vllm)
        conn.logger.bind(tag=TAG).info(
            "vision runtime config: "
            f"current_agent_id={getattr(conn, 'current_agent_id', None)}, "
            f"selected_vllm={selected_vllm}, "
            f"selected_module_keys={list(selected_module.keys())}, "
            f"vllm_keys={list(vllm_map.keys())}"
        )
        if selected_vllm and vllm_config:
            vllm_type = vllm_config.get("type", selected_vllm)
            conn.logger.bind(tag=TAG).info(
                f"vision runtime using direct VLLM: provider={vllm_type}, model={vllm_config.get('model_name')}"
            )
            provider = vllm_utils.create_instance(vllm_type, vllm_config)
            return provider.response(question, image_base64)

        vision_url = get_vision_url(conn.config)
        if not vision_url:
            raise RuntimeError("未配置视觉识别服务")
        conn.logger.bind(tag=TAG).warning(
            f"vision runtime fallback to vision_explain: url={vision_url}"
        )

        response = requests.post(
            vision_url,
            json={
                "question": question,
                "image_base64": image_base64,
                "image_mime": image_mime,
            },
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()

        if isinstance(payload, dict):
            for key in ("text", "content", "result", "response"):
                if payload.get(key):
                    return str(payload[key])
            data = payload.get("data")
            if isinstance(data, dict):
                for key in ("text", "content", "result", "response"):
                    if data.get(key):
                        return str(data[key])
        return json.dumps(payload, ensure_ascii=False)

    def _parse_visual_response(self, raw_response: str) -> Dict[str, Any]:
        if not raw_response:
            return {
                "vision_summary": "图片内容暂时无法确认",
                "vision_entities": [],
                "vision_emotion_hint": "无可靠视觉线索",
            }

        candidate = raw_response.strip()
        if candidate.startswith("```"):
            candidate = candidate.strip("`")
            if "\n" in candidate:
                candidate = candidate.split("\n", 1)[1]
        try:
            data = json.loads(candidate)
        except Exception:
            extracted = extract_json_from_string(candidate)
            if extracted:
                try:
                    data = json.loads(extracted)
                except Exception:
                    data = None
            else:
                data = None

        if isinstance(data, dict):
            entities = data.get("vision_entities") or []
            if not isinstance(entities, list):
                entities = [str(entities)]
            return {
                "vision_summary": str(data.get("vision_summary") or "图片内容已识别"),
                "vision_entities": [str(item) for item in entities[:3] if str(item).strip()],
                "vision_emotion_hint": str(
                    data.get("vision_emotion_hint") or "仅可作为弱视觉线索"
                ),
            }

        return {
            "vision_summary": candidate[:160],
            "vision_entities": [],
            "vision_emotion_hint": "仅可作为弱视觉线索",
            "source": "vision_text_fallback",
        }
