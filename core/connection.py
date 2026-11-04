import os
import re
import sys
import copy
import json
import uuid
import time
import queue
import shutil
import asyncio
import threading
import traceback
import subprocess
import websockets
from time import perf_counter

from core.utils.util import (
    extract_json_from_string,
    check_vad_update,
    check_asr_update,
    filter_sensitive_info,
)
from typing import Dict, Any
from collections import deque
from core.utils.modules_initialize import (
    initialize_modules,
    initialize_tts,
    initialize_asr,
)
from core.handle.reportHandle import report, report_with_audio_bytes
from core.providers.tts.default import DefaultTTS
from concurrent.futures import ThreadPoolExecutor
from core.utils.dialogue import Message, Dialogue
from core.providers.asr.dto.dto import InterfaceType
from core.handle.textHandle import handleTextMessage
from core.providers.tools.unified_tool_handler import UnifiedToolHandler
from plugins_func.loadplugins import auto_import_modules
from plugins_func.register import Action
from core.auth import AuthenticationError
from config.config_loader import get_private_config_from_api
from core.providers.tts.dto.dto import ContentType, TTSMessageDTO, SentenceType
from config.logger import setup_logging, build_module_string, create_connection_logger
from config.manage_api_client import DeviceNotFoundException, DeviceBindException
from core.utils.prompt_manager import PromptManager
from core.utils.voiceprint_provider import VoiceprintProvider
from core.utils import textUtils
from core.utils.speech_planner import (
    DEFAULT_STYLE,
    MIMO_TTS_PROMPT_RULES,
    build_speech_plan,
    build_mimo_prosody_system_prompt,
    build_mimo_prosody_user_prompt,
    extract_mimo_style,
    extract_mimo_tts_text,
    is_invalid_mimo_rewrite,
)
from integration.unified_service import UnifiedServiceClient
from core.utils.client_context import (
    build_episodic_turn_context,
    build_memory_scope_key,
    build_reply_meta,
    derive_ui_state_hint,
)
from core.utils.sentence_pipeline import (
    ReplyTurn,
    SentenceUnit,
    pcm16_base64_chunks_duration_ms,
    pcm16_base64_chunks_to_wav_bytes,
    split_sentence_buffer,
)

TAG = __name__

auto_import_modules("plugins_func.functions")


class TTSException(RuntimeError):
    pass


class ConnectionHandler:
    _uniapp_cleanup_started = False
    _uniapp_cleanup_lock = threading.Lock()

    def __init__(
        self,
        config: Dict[str, Any],
        _vad,
        _asr,
        _llm,
        _memory,
        _intent,
        server=None,
    ):
        self.common_config = config
        self.config = copy.deepcopy(config)
        self.session_id = str(uuid.uuid4())
        self.session_id_adopted = False
        self.logger = setup_logging()
        self.server = server  # 保存server实例的引用

        self.need_bind = False
        self.bind_code = None
        self.read_config_from_api = self.config.get("read_config_from_api", False)

        self.websocket = None
        self.headers = None
        self.device_id = None
        self.client_ip = None
        self.prompt = None
        self.welcome_msg = None
        self.max_output_size = 0
        self.chat_history_conf = 0
        self.audio_format = "opus"
        self.is_uniapp_client = False
        self.enable_audio_response = False
        self.uniapp_audio_files = []

        # 客户端状态相关
        self.client_abort = False
        self.client_is_speaking = False
        self.client_listen_mode = "auto"

        # 线程任务相关
        self.loop = asyncio.get_event_loop()
        self.stop_event = threading.Event()
        self.executor = ThreadPoolExecutor(max_workers=5)

        # 添加上报线程池
        self.report_queue = queue.Queue()
        self.report_thread = None
        # 未来可以通过修改此处，调节asr的上报和tts的上报，目前默认都开启
        self.report_asr_enable = self.read_config_from_api
        self.report_tts_enable = self.read_config_from_api

        # 依赖的组件
        self.vad = None
        self.asr = None
        self.tts = None
        self._asr = _asr
        self._vad = _vad
        self.llm = _llm
        self.memory = _memory
        self.intent = _intent

        # 为每个连接单独管理声纹识别
        self.voiceprint_provider = None

        # vad相关变量
        self.client_audio_buffer = bytearray()
        self.client_have_voice = False
        self.last_activity_time = 0.0  # 统一的活动时间戳（毫秒）
        self.client_voice_stop = False
        self.client_voice_window = deque(maxlen=5)
        self.last_is_voice = False

        # asr相关变量
        # 因为实际部署时可能会用到公共的本地ASR，不能把变量暴露给公共ASR
        # 所以涉及到ASR的变量，需要在这里定义，属于connection的私有变量
        self.asr_audio = []
        self.asr_audio_queue = queue.Queue()

        # llm相关变量
        self.llm_finish_task = True
        self.dialogue = Dialogue()
        self.dialogue_policy_state = {}
        self.last_policy = {}
        self.pending_visual_context = None
        self.entry_context = {}

        # tts相关变量
        self.sentence_id = None
        # 处理TTS响应没有文本返回
        self.tts_MessageText = ""

        # iot相关变量
        self.iot_descriptors = {}
        self.func_handler = None

        self.cmd_exit = self.config["exit_commands"]

        # 是否在聊天结束后关闭连接
        self.close_after_chat = False
        self.load_function_plugin = False
        self.intent_type = "nointent"

        self.timeout_seconds = (
            int(self.config.get("close_connection_no_voice_time", 120)) + 60
        )  # 在原来第一道关闭的基础上加60秒，进行二道关闭
        self.timeout_task = None

        # {"mcp":true} 表示启用MCP功能
        self.features = None

        # 标记连接是否来自MQTT
        self.conn_from_mqtt_gateway = False

        # 初始化提示词管理器
        self.prompt_manager = PromptManager(config, self.logger)
        self.current_agent_id = None
        self.current_reply_turn: ReplyTurn | None = None
        self.reply_turn_lock = threading.Lock()
        self._pending_sentence_audio_files = set()
        self._active_reply_message_ids = set()
        self._sentence_pipeline_config = self._load_sentence_pipeline_config()
        self._rewrite_slots = threading.BoundedSemaphore(
            value=max(1, self._sentence_pipeline_config["max_rewrite_workers"])
        )
        self._tts_slots = threading.BoundedSemaphore(
            value=max(1, self._sentence_pipeline_config["max_tts_workers"])
        )
        self._ensure_uniapp_audio_cleanup_worker()

    def _load_sentence_pipeline_config(self) -> Dict[str, Any]:
        pipeline_conf = self.config.get("sentence_pipeline", {}) or {}
        return {
            "max_pending_sentences": int(
                pipeline_conf.get("max_pending_sentences", 4)
            ),
            "max_rewrite_workers": int(pipeline_conf.get("max_rewrite_workers", 1)),
            "max_tts_workers": int(pipeline_conf.get("max_tts_workers", 2)),
            "max_pending_audio_jobs": int(
                pipeline_conf.get("max_pending_audio_jobs", 3)
            ),
            "rewrite_timeout": float(pipeline_conf.get("rewrite_timeout", 2.5)),
            "max_rewrite_sentences": int(
                pipeline_conf.get("max_rewrite_sentences", 0)
            ),
            "rewrite_min_chars": int(pipeline_conf.get("rewrite_min_chars", 12)),
            "min_sentence_chars": int(
                pipeline_conf.get("min_sentence_chars", 8)
            ),
            "sentence_soft_limit": int(
                pipeline_conf.get("sentence_soft_limit", 16)
            ),
            "sentence_hard_limit": int(
                pipeline_conf.get("sentence_hard_limit", 28)
            ),
            "immersive_sentence_soft_limit": int(
                pipeline_conf.get("immersive_sentence_soft_limit", 10)
            ),
            "immersive_sentence_hard_limit": int(
                pipeline_conf.get("immersive_sentence_hard_limit", 18)
            ),
            "cleanup_enabled": bool(
                self.config.get("uniapp_audio_cleanup_enabled", True)
            ),
            "cleanup_interval_seconds": int(
                self.config.get("uniapp_audio_cleanup_interval_seconds", 300)
            ),
            "retention_seconds": int(
                self.config.get("uniapp_audio_retention_seconds", 1800)
            ),
        }

    def _ensure_uniapp_audio_cleanup_worker(self) -> None:
        if not self._sentence_pipeline_config.get("cleanup_enabled", True):
            return
        with ConnectionHandler._uniapp_cleanup_lock:
            if ConnectionHandler._uniapp_cleanup_started:
                return
            worker = threading.Thread(
                target=self._uniapp_audio_cleanup_worker,
                daemon=True,
            )
            worker.start()
            ConnectionHandler._uniapp_cleanup_started = True

    def _uniapp_audio_cleanup_worker(self) -> None:
        cleanup_interval = max(
            60, int(self._sentence_pipeline_config.get("cleanup_interval_seconds", 300))
        )
        while True:
            try:
                self._cleanup_expired_uniapp_audio_files()
            except Exception as e:
                self.logger.bind(tag=TAG).warning(
                    f"uniapp audio cleanup worker failed: {e}"
                )
            time.sleep(cleanup_interval)

    def _cleanup_expired_uniapp_audio_files(self) -> None:
        audio_root = self.config.get("uniapp_audio_dir", "data/uniapp_audio")
        retention = max(
            60, int(self._sentence_pipeline_config.get("retention_seconds", 1800))
        )
        if not os.path.isdir(audio_root):
            return

        now = time.time()
        for session_dir in os.listdir(audio_root):
            session_path = os.path.join(audio_root, session_dir)
            if not os.path.isdir(session_path):
                continue
            for message_dir in os.listdir(session_path):
                message_path = os.path.join(session_path, message_dir)
                if not os.path.isdir(message_path):
                    continue
                try:
                    last_mtime = os.path.getmtime(message_path)
                except OSError:
                    continue
                if now - last_mtime > retention:
                    shutil.rmtree(message_path, ignore_errors=True)
            if not os.path.exists(session_path):
                continue
            try:
                if not os.listdir(session_path):
                    os.rmdir(session_path)
            except OSError:
                pass

    def _is_immersive_mode(self) -> bool:
        entry_context = getattr(self, "entry_context", {}) or {}
        return bool(entry_context.get("immersive_mode"))

    def _get_sentence_limits(self) -> tuple[int, int]:
        if self._is_immersive_mode():
            return (
                self._sentence_pipeline_config["immersive_sentence_soft_limit"],
                self._sentence_pipeline_config["immersive_sentence_hard_limit"],
            )
        return (
            self._sentence_pipeline_config["sentence_soft_limit"],
            self._sentence_pipeline_config["sentence_hard_limit"],
        )

    async def handle_connection(self, ws):
        try:
            # 获取并验证headers
            self.headers = dict(ws.request.headers)
            real_ip = self.headers.get("x-real-ip") or self.headers.get(
                "x-forwarded-for"
            )
            if real_ip:
                self.client_ip = real_ip.split(",")[0].strip()
            else:
                self.client_ip = ws.remote_address[0]
            self.logger.bind(tag=TAG).info(
                f"{self.client_ip} conn - Headers: {self.headers}"
            )

            self.device_id = self.headers.get("device-id", None)
            self.current_agent_id = self.headers.get("agent-id", None)
            if self.device_id and str(self.device_id).startswith("uniapp_"):
                self.is_uniapp_client = True
                self.enable_audio_response = True
                self.uniapp_audio_files = []

            # 认证通过,继续处理
            self.websocket = ws

            # 检查是否来自MQTT连接
            request_path = ws.request.path
            self.conn_from_mqtt_gateway = request_path.endswith("?from=mqtt_gateway")
            if self.conn_from_mqtt_gateway:
                self.logger.bind(tag=TAG).info("连接来自:MQTT网关")

            # 初始化活动时间戳
            self.last_activity_time = time.time() * 1000

            # 启动超时检查任务
            self.timeout_task = asyncio.create_task(self._check_timeout())

            self.welcome_msg = self.config["xiaozhi"]
            self.welcome_msg["session_id"] = self.session_id

            # 获取差异化配置
            self._initialize_private_config()
            # 异步初始化
            self.executor.submit(self._initialize_components)

            try:
                async for message in self.websocket:
                    await self._route_message(message)
            except websockets.exceptions.ConnectionClosed:
                self.logger.bind(tag=TAG).info("客户端断开连接")

        except AuthenticationError as e:
            self.logger.bind(tag=TAG).error(f"Authentication failed: {str(e)}")
            return
        except Exception as e:
            stack_trace = traceback.format_exc()
            self.logger.bind(tag=TAG).error(f"Connection error: {str(e)}-{stack_trace}")
            return
        finally:
            try:
                await self._save_and_close(ws)
            except Exception as final_error:
                self.logger.bind(tag=TAG).error(f"最终清理时出错: {final_error}")
                # 确保即使保存记忆失败，也要关闭连接
                try:
                    await self.close(ws)
                except Exception as close_error:
                    self.logger.bind(tag=TAG).error(
                        f"强制关闭连接时出错: {close_error}"
                    )

    async def _save_and_close(self, ws):
        """保存记忆并关闭连接"""
        try:
            if self.memory:
                # 使用线程池异步保存记忆
                def save_memory_task():
                    try:
                        # 创建新事件循环（避免与主循环冲突）
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        loop.run_until_complete(
                            self.memory.save_memory(self.dialogue.dialogue)
                        )
                    except Exception as e:
                        self.logger.bind(tag=TAG).error(f"保存记忆失败: {e}")
                    finally:
                        try:
                            loop.close()
                        except Exception:
                            pass

                # 启动线程保存记忆，不等待完成
                threading.Thread(target=save_memory_task, daemon=True).start()
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"保存记忆失败: {e}")
        finally:
            # 立即关闭连接，不等待记忆保存完成
            try:
                await self.close(ws)
            except Exception as close_error:
                self.logger.bind(tag=TAG).error(
                    f"保存记忆后关闭连接失败: {close_error}"
                )

    async def _route_message(self, message):
        """消息路由"""
        if isinstance(message, str):
            await handleTextMessage(self, message)
        elif isinstance(message, bytes):
            if self.vad is None or self.asr is None:
                return

            # 处理来自MQTT网关的音频包
            if self.conn_from_mqtt_gateway and len(message) >= 16:
                handled = await self._process_mqtt_audio_message(message)
                if handled:
                    return

            # 不需要头部处理或没有头部时，直接处理原始消息
            self.asr_audio_queue.put(message)

    async def _process_mqtt_audio_message(self, message):
        """
        处理来自MQTT网关的音频消息，解析16字节头部并提取音频数据

        Args:
            message: 包含头部的音频消息

        Returns:
            bool: 是否成功处理了消息
        """
        try:
            # 提取头部信息
            timestamp = int.from_bytes(message[8:12], "big")
            audio_length = int.from_bytes(message[12:16], "big")

            # 提取音频数据
            if audio_length > 0 and len(message) >= 16 + audio_length:
                # 有指定长度，提取精确的音频数据
                audio_data = message[16 : 16 + audio_length]
                # 基于时间戳进行排序处理
                self._process_websocket_audio(audio_data, timestamp)
                return True
            elif len(message) > 16:
                # 没有指定长度或长度无效，去掉头部后处理剩余数据
                audio_data = message[16:]
                self.asr_audio_queue.put(audio_data)
                return True
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"解析WebSocket音频包失败: {e}")

        # 处理失败，返回False表示需要继续处理
        return False

    def _process_websocket_audio(self, audio_data, timestamp):
        """处理WebSocket格式的音频包"""
        # 初始化时间戳序列管理
        if not hasattr(self, "audio_timestamp_buffer"):
            self.audio_timestamp_buffer = {}
            self.last_processed_timestamp = 0
            self.max_timestamp_buffer_size = 20

        # 如果时间戳是递增的，直接处理
        if timestamp >= self.last_processed_timestamp:
            self.asr_audio_queue.put(audio_data)
            self.last_processed_timestamp = timestamp

            # 处理缓冲区中的后续包
            processed_any = True
            while processed_any:
                processed_any = False
                for ts in sorted(self.audio_timestamp_buffer.keys()):
                    if ts > self.last_processed_timestamp:
                        buffered_audio = self.audio_timestamp_buffer.pop(ts)
                        self.asr_audio_queue.put(buffered_audio)
                        self.last_processed_timestamp = ts
                        processed_any = True
                        break
        else:
            # 乱序包，暂存
            if len(self.audio_timestamp_buffer) < self.max_timestamp_buffer_size:
                self.audio_timestamp_buffer[timestamp] = audio_data
            else:
                self.asr_audio_queue.put(audio_data)

    async def handle_restart(self, message):
        """处理服务器重启请求"""
        try:

            self.logger.bind(tag=TAG).info("收到服务器重启指令，准备执行...")

            # 发送确认响应
            await self.websocket.send(
                json.dumps(
                    {
                        "type": "server",
                        "status": "success",
                        "message": "服务器重启中...",
                        "content": {"action": "restart"},
                    }
                )
            )

            # 异步执行重启操作
            def restart_server():
                """实际执行重启的方法"""
                time.sleep(1)
                self.logger.bind(tag=TAG).info("执行服务器重启...")
                subprocess.Popen(
                    [sys.executable, "app.py"],
                    stdin=sys.stdin,
                    stdout=sys.stdout,
                    stderr=sys.stderr,
                    start_new_session=True,
                )
                os._exit(0)

            # 使用线程执行重启避免阻塞事件循环
            threading.Thread(target=restart_server, daemon=True).start()

        except Exception as e:
            self.logger.bind(tag=TAG).error(f"重启失败: {str(e)}")
            await self.websocket.send(
                json.dumps(
                    {
                        "type": "server",
                        "status": "error",
                        "message": f"Restart failed: {str(e)}",
                        "content": {"action": "restart"},
                    }
                )
            )

    def _initialize_components(self):
        try:
            self.selected_module_str = build_module_string(
                self.config.get("selected_module", {})
            )
            self.logger = create_connection_logger(self.selected_module_str)

            """初始化组件"""
            if self.config.get("prompt") is not None:
                user_prompt = self.config["prompt"]
                # 使用快速提示词进行初始化
                prompt = self.prompt_manager.get_quick_prompt(user_prompt)
                self.change_system_prompt(prompt)
                self.logger.bind(tag=TAG).info(
                    f"快速初始化组件: prompt成功 {prompt[:50]}..."
                )

            """初始化本地组件"""
            if self.vad is None:
                self.vad = self._vad
            if self.asr is None:
                self.asr = self._initialize_asr()

            # 初始化声纹识别
            self._initialize_voiceprint()

            # 打开语音识别通道
            asyncio.run_coroutine_threadsafe(
                self.asr.open_audio_channels(self), self.loop
            )
            if self.tts is None:
                self.tts = self._initialize_tts()
            # 打开语音合成通道
            asyncio.run_coroutine_threadsafe(
                self.tts.open_audio_channels(self), self.loop
            )

            """加载记忆"""
            self._initialize_memory()
            """加载意图识别"""
            self._initialize_intent()
            """初始化上报线程"""
            self._init_report_threads()
            """更新系统提示词"""
            self._init_prompt_enhancement()

        except Exception as e:
            self.logger.bind(tag=TAG).error(f"实例化组件失败: {e}")

    def _init_prompt_enhancement(self):
        # 更新上下文信息
        self.prompt_manager.update_context_info(self, self.client_ip)
        enhanced_prompt = self.prompt_manager.build_enhanced_prompt(
            self.config["prompt"], self.device_id, self.client_ip
        )
        if enhanced_prompt:
            self.change_system_prompt(enhanced_prompt)
            self.logger.bind(tag=TAG).info("系统提示词已增强更新")

    def _init_report_threads(self):
        """初始化ASR和TTS上报线程"""
        if not self.read_config_from_api or self.need_bind:
            return
        if self.chat_history_conf == 0:
            return
        if self.report_thread is None or not self.report_thread.is_alive():
            self.report_thread = threading.Thread(
                target=self._report_worker, daemon=True
            )
            self.report_thread.start()
            self.logger.bind(tag=TAG).info("TTS上报线程已启动")

    def _initialize_tts(self):
        """初始化TTS"""
        tts = None
        if not self.need_bind:
            tts = initialize_tts(self.config)

        if tts is None:
            tts = DefaultTTS(self.config, delete_audio_file=True)

        return tts

    def _initialize_asr(self):
        """初始化ASR"""
        if self._asr.interface_type == InterfaceType.LOCAL:
            # 如果公共ASR是本地服务，则直接返回
            # 因为本地一个实例ASR，可以被多个连接共享
            asr = self._asr
        else:
            # 如果公共ASR是远程服务，则初始化一个新实例
            # 因为远程ASR，涉及到websocket连接和接收线程，需要每个连接一个实例
            asr = initialize_asr(self.config)

        return asr

    def _initialize_voiceprint(self):
        """为当前连接初始化声纹识别"""
        try:
            voiceprint_config = self.config.get("voiceprint", {})
            if voiceprint_config:
                voiceprint_provider = VoiceprintProvider(voiceprint_config)
                if voiceprint_provider is not None and voiceprint_provider.enabled:
                    self.voiceprint_provider = voiceprint_provider
                    self.logger.bind(tag=TAG).info("声纹识别功能已在连接时动态启用")
                else:
                    self.logger.bind(tag=TAG).warning("声纹识别功能启用但配置不完整")
            else:
                self.logger.bind(tag=TAG).info("声纹识别功能未启用")
        except Exception as e:
            self.logger.bind(tag=TAG).warning(f"声纹识别初始化失败: {str(e)}")

    def _initialize_private_config(self):
        """如果是从配置文件获取，则进行二次实例化"""
        if not self.read_config_from_api:
            return
        """从接口获取差异化的配置进行二次实例化，非全量重新实例化"""
        device_id = self.headers.get("device-id")
        is_uniapp_device = bool(device_id and device_id.startswith("uniapp_"))
        try:
            begin_time = time.time()
            
            # ========== UniApp 设备自动创建 ==========
            device_created = False
            
            # 如果是 UniApp 设备且启用自动创建，则先尝试创建设备
            if device_id and device_id.startswith("uniapp_"):
                auto_create_enabled = self.config.get("auto_create_uniapp_device", True)
                if auto_create_enabled:
                    try:
                        result = self._auto_create_uniapp_device(device_id)
                        device_created = result and result.get('created', False)
                    except Exception as create_error:
                        self.logger.bind(tag=TAG).warning(
                            f"自动创建 UniApp 设备时出错: {create_error}"
                        )
            # ==========================================
            
            # 获取差异化配置，如果刚创建了设备，稍微延迟一下并重试
            max_retries = 3 if device_created else 1
            retry_delay = 0.5  # 500ms
            private_config = {}  # 初始化为空字典
            
            for attempt in range(max_retries):
                try:
                    private_config = get_private_config_from_api(
                        self.config,
                        self.headers.get("device-id"),
                        self.headers.get("client-id", self.headers.get("device-id")),
                    )
                    break  # 成功获取，退出重试循环
                except DeviceNotFoundException as e:
                    if attempt < max_retries - 1:
                        # 还有重试机会，等待后重试
                        self.logger.bind(tag=TAG).warning(
                            f"设备未找到，{retry_delay}秒后重试 ({attempt + 1}/{max_retries})..."
                        )
                        time.sleep(retry_delay)
                        continue
                    else:
                        # UniApp 走软件客户端链路，不进入硬件绑定语义
                        if not is_uniapp_device:
                            self.need_bind = True
                        private_config = {}
                        break
            
            # 如果成功获取了配置（不为空），添加 delete_audio 配置
            if private_config:
                private_config["delete_audio"] = bool(self.config.get("delete_audio", True))
                self.logger.bind(tag=TAG).info(
                    f"{time.time() - begin_time} 秒，获取差异化配置成功: {json.dumps(filter_sensitive_info(private_config), ensure_ascii=False)}"
                )
            else:
                self.logger.bind(tag=TAG).warning("未能获取差异化配置，将使用默认配置")
                
        except DeviceBindException as e:
            if not is_uniapp_device:
                self.need_bind = True
                self.bind_code = e.bind_code
            else:
                self.logger.bind(tag=TAG).warning(
                    f"UniApp 设备忽略硬件绑定异常，降级使用当前配置: {e}"
                )
            private_config = {}
        except Exception as e:
            if not is_uniapp_device:
                self.need_bind = True
                self.logger.bind(tag=TAG).error(f"获取差异化配置失败: {e}")
            else:
                self.logger.bind(tag=TAG).warning(
                    f"UniApp 私有配置获取失败，继续使用当前配置: {e}"
                )
            private_config = {}

        init_llm, init_tts, init_memory, init_intent = (
            False,
            False,
            False,
            False,
        )

        init_vad = check_vad_update(self.common_config, private_config)
        init_asr = check_asr_update(self.common_config, private_config)

        if init_vad:
            self.config["VAD"] = private_config["VAD"]
            self.config["selected_module"]["VAD"] = private_config["selected_module"][
                "VAD"
            ]
        if init_asr:
            self.config["ASR"] = private_config["ASR"]
            self.config["selected_module"]["ASR"] = private_config["selected_module"][
                "ASR"
            ]
        if private_config.get("TTS", None) is not None:
            init_tts = True
            # 保存本地TTS配置（包含API密钥等敏感信息）
            local_tts_config = copy.deepcopy(self.config.get("TTS", {}))
            
            # 更新TTS配置
            self.config["TTS"] = private_config["TTS"]
            self.config["selected_module"]["TTS"] = private_config["selected_module"][
                "TTS"
            ]
            
            # 合并配置：将本地配置中的敏感字段合并到API配置中
            selected_tts_module = self.config["selected_module"]["TTS"]
            if selected_tts_module in self.config["TTS"]:
                api_tts_config = self.config["TTS"][selected_tts_module]
                local_tts_module_config = local_tts_config.get(selected_tts_module, {})
                
                # 需要从本地配置保留的字段列表
                preserve_fields = [
                    "api_key", "access_token", "appid", "app_id",
                    "authorization", "token", "secret", "api_url"
                ]
                
                # 合并配置
                for field in preserve_fields:
                    if field in local_tts_module_config:
                        local_value = local_tts_module_config[field]
                        if local_value and isinstance(local_value, str) and "你的" not in local_value:
                            api_value = api_tts_config.get(field, "")
                            if not api_value or "你的" in str(api_value):
                                api_tts_config[field] = local_value
                                self.logger.bind(tag=TAG).info(
                                    f"从本地配置保留 TTS 的 {field} 字段"
                                )
        if private_config.get("LLM", None) is not None:
            init_llm = True
            # 保存本地LLM配置（包含API密钥等敏感信息）
            local_llm_config = copy.deepcopy(self.config.get("LLM", {}))
            
            # 更新LLM配置
            self.config["LLM"] = private_config["LLM"]
            self.config["selected_module"]["LLM"] = private_config["selected_module"][
                "LLM"
            ]
            
            # 合并配置：将本地配置中的敏感字段（如api_key、url、base_url等）合并到API配置中
            selected_llm_module = self.config["selected_module"]["LLM"]
            if selected_llm_module in self.config["LLM"]:
                api_llm_config = self.config["LLM"][selected_llm_module]
                local_llm_module_config = local_llm_config.get(selected_llm_module, {})
                
                # 需要从本地配置保留的字段列表
                preserve_fields = [
                    "api_key", "access_token", "secret_key", "secret_id",
                    "appid", "app_id", "url", "base_url", "api_url",
                    "ws_url", "host", "token", "personal_access_token",
                    "authorization", "secret"
                ]
                
                # 合并配置
                for field in preserve_fields:
                    if field in local_llm_module_config:
                        local_value = local_llm_module_config[field]
                        # 只有当本地配置有效（不是占位符）时才保留
                        if local_value and isinstance(local_value, str) and "你的" not in local_value:
                            # 如果API配置中没有该字段，或者API配置中的值是占位符，则使用本地值
                            api_value = api_llm_config.get(field, "")
                            if not api_value or "你的" in str(api_value):
                                api_llm_config[field] = local_value
                                self.logger.bind(tag=TAG).info(
                                    f"从本地配置保留 LLM 的 {field} 字段"
                                )
        if private_config.get("VLLM", None) is not None:
            self.config["VLLM"] = private_config["VLLM"]
            self.config["selected_module"]["VLLM"] = private_config["selected_module"][
                "VLLM"
            ]
        if private_config.get("Memory", None) is not None:
            init_memory = True
            self.config["Memory"] = private_config["Memory"]
            self.config["selected_module"]["Memory"] = private_config[
                "selected_module"
            ]["Memory"]
        if private_config.get("Intent", None) is not None:
            init_intent = True
            self.config["Intent"] = private_config["Intent"]
            model_intent = private_config.get("selected_module", {}).get("Intent", {})
            self.config["selected_module"]["Intent"] = model_intent
            # 加载插件配置
            if model_intent != "Intent_nointent":
                plugin_from_server = private_config.get("plugins", {})
                for plugin, config_str in plugin_from_server.items():
                    plugin_from_server[plugin] = json.loads(config_str)
                self.config["plugins"] = plugin_from_server
                self.config["Intent"][self.config["selected_module"]["Intent"]][
                    "functions"
                ] = plugin_from_server.keys()
        if private_config.get("prompt", None) is not None:
            self.config["prompt"] = private_config["prompt"]
        # 获取声纹信息
        if private_config.get("voiceprint", None) is not None:
            self.config["voiceprint"] = private_config["voiceprint"]
        if private_config.get("summaryMemory", None) is not None:
            self.config["summaryMemory"] = private_config["summaryMemory"]
        if private_config.get("device_max_output_size", None) is not None:
            self.max_output_size = int(private_config["device_max_output_size"])
        if private_config.get("chat_history_conf", None) is not None:
            self.chat_history_conf = int(private_config["chat_history_conf"])
        if private_config.get("mcp_endpoint", None) is not None:
            self.config["mcp_endpoint"] = private_config["mcp_endpoint"]
        agent_id_from_config = private_config.get("agentId") or private_config.get("agent_id")
        if agent_id_from_config:
            self.current_agent_id = str(agent_id_from_config)
        # Allow manager-api agent config to control dialogue policy per agent.
        if private_config.get("dialogue_policy", None) is not None:
            policy_from_server = private_config.get("dialogue_policy")
            if isinstance(policy_from_server, dict):
                local_policy = self.config.get("dialogue_policy", {}) or {}
                if not isinstance(local_policy, dict):
                    local_policy = {}
                merged_policy = dict(local_policy)
                merged_policy.update(policy_from_server)
                self.config["dialogue_policy"] = merged_policy
                self.logger.bind(tag=TAG).info(
                    f"Applied dialogue_policy from private config: {merged_policy}"
                )
        try:
            selected_tts = self.config.get("selected_module", {}).get("TTS")
            selected_tts_conf = self.config.get("TTS", {}).get(selected_tts, {}) if selected_tts else {}
            self.logger.bind(tag=TAG).info(
                "final TTS config resolved: selected=%s type=%s has_api_key=%s api_url=%s",
                selected_tts,
                selected_tts_conf.get("type"),
                bool(selected_tts_conf.get("api_key")),
                selected_tts_conf.get("api_url"),
            )
        except Exception as e:
            self.logger.bind(tag=TAG).warning(f"打印最终TTS配置失败: {e}")
        try:
            modules = initialize_modules(
                self.logger,
                self.config,
                init_vad,
                init_asr,
                init_llm,
                init_tts,
                init_memory,
                init_intent,
            )
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"初始化组件失败: {e}")
            modules = {}
        if modules.get("tts", None) is not None:
            self.tts = modules["tts"]
        if modules.get("vad", None) is not None:
            self.vad = modules["vad"]
        if modules.get("asr", None) is not None:
            self.asr = modules["asr"]
        if modules.get("llm", None) is not None:
            self.llm = modules["llm"]
        if modules.get("intent", None) is not None:
            self.intent = modules["intent"]
        if modules.get("memory", None) is not None:
            self.memory = modules["memory"]

    def _initialize_memory(self):
        if self.memory is None:
            return
        """初始化记忆模块"""
        role_id = self._build_memory_scope_key()
        self.memory.init_memory(
            role_id=role_id,
            llm=self.llm,
            summary_memory=self.config.get("summaryMemory", None),
            save_to_file=not self.read_config_from_api,
        )
        self.current_memory_scope_key = role_id

        # 获取记忆总结配置
        memory_config = self.config["Memory"]
        memory_type = self.config["Memory"][self.config["selected_module"]["Memory"]][
            "type"
        ]
        # 如果使用 nomen，直接返回
        if memory_type == "nomem":
            return
        # 使用 mem_local_short 模式
        elif memory_type == "mem_local_short":
            memory_llm_name = memory_config[self.config["selected_module"]["Memory"]][
                "llm"
            ]
            if memory_llm_name and memory_llm_name in self.config["LLM"]:
                # 如果配置了专用LLM，则创建独立的LLM实例
                from core.utils import llm as llm_utils

                memory_llm_config = self.config["LLM"][memory_llm_name]
                memory_llm_type = memory_llm_config.get("type", memory_llm_name)
                memory_llm = llm_utils.create_instance(
                    memory_llm_type, memory_llm_config
                )
                self.logger.bind(tag=TAG).info(
                    f"为记忆总结创建了专用LLM: {memory_llm_name}, 类型: {memory_llm_type}"
                )
                self.memory.set_llm(memory_llm)
            else:
                # 否则使用主LLM
                self.memory.set_llm(self.llm)
                self.logger.bind(tag=TAG).info("使用主LLM作为意图识别模型")

    def _build_memory_scope_key(self) -> str | None:
        return build_memory_scope_key(
            getattr(self, "device_id", None),
            getattr(self, "current_agent_id", None),
        )

    def _ensure_memory_scope(self) -> str | None:
        memory_scope_key = self._build_memory_scope_key()
        self.current_memory_scope_key = memory_scope_key
        if self.memory is None or not memory_scope_key:
            return memory_scope_key
        previous_role_id = getattr(self.memory, "role_id", None)
        if previous_role_id == memory_scope_key:
            return memory_scope_key
        try:
            self.memory.init_memory(
                role_id=memory_scope_key,
                llm=self.llm,
                summary_memory=self.config.get("summaryMemory", None),
                save_to_file=not self.read_config_from_api,
            )
            self.logger.bind(tag=TAG).info(
                f"memory scope rebound: {previous_role_id} -> {memory_scope_key}"
            )
        except Exception as e:
            self.logger.bind(tag=TAG).warning(f"memory scope rebind failed: {e}")
        return memory_scope_key

    def _initialize_intent(self):
        if self.intent is None:
            return
        self.intent_type = self.config["Intent"][
            self.config["selected_module"]["Intent"]
        ]["type"]
        if self.intent_type == "function_call" or self.intent_type == "intent_llm":
            self.load_function_plugin = True
        """初始化意图识别模块"""
        # 获取意图识别配置
        intent_config = self.config["Intent"]
        intent_type = self.config["Intent"][self.config["selected_module"]["Intent"]][
            "type"
        ]

        # 如果使用 nointent，直接返回
        if intent_type == "nointent":
            return
        # 使用 intent_llm 模式
        elif intent_type == "intent_llm":
            intent_llm_name = intent_config[self.config["selected_module"]["Intent"]][
                "llm"
            ]

            if intent_llm_name and intent_llm_name in self.config["LLM"]:
                # 如果配置了专用LLM，则创建独立的LLM实例
                from core.utils import llm as llm_utils

                intent_llm_config = self.config["LLM"][intent_llm_name]
                intent_llm_type = intent_llm_config.get("type", intent_llm_name)
                intent_llm = llm_utils.create_instance(
                    intent_llm_type, intent_llm_config
                )
                self.logger.bind(tag=TAG).info(
                    f"为意图识别创建了专用LLM: {intent_llm_name}, 类型: {intent_llm_type}"
                )
                self.intent.set_llm(intent_llm)
            else:
                # 否则使用主LLM
                self.intent.set_llm(self.llm)
                self.logger.bind(tag=TAG).info("使用主LLM作为意图识别模型")

        """加载统一工具处理器"""
        self.func_handler = UnifiedToolHandler(self)

        # 异步初始化工具处理器
        if hasattr(self, "loop") and self.loop:
            asyncio.run_coroutine_threadsafe(self.func_handler._initialize(), self.loop)

    def _auto_create_uniapp_device(self, device_id: str):
        """
        自动创建 UniApp 设备
        
        Args:
            device_id: 设备ID，格式为 uniapp_<user_id>
        """
        if not device_id or not device_id.startswith("uniapp_"):
            return
        
        # 提取 user_id
        user_id = device_id[7:]  # 去掉 'uniapp_' 前缀
        
        if not user_id:
            self.logger.bind(tag=TAG).warning("无效的 UniApp 设备ID，缺少用户标识")
            return
        
        try:
            import sys
            sys.path.insert(0, '/opt/xiaozhi-esp32-server')
            from config.manage_api_client import create_uniapp_device_if_not_exists
            
            result = create_uniapp_device_if_not_exists(
                self.config,
                device_id,
                user_id
            )

            agent_id = result.get('agent_id') if isinstance(result, dict) else None
            if agent_id:
                self.current_agent_id = str(agent_id)
            
            if result.get('created'):
                self.logger.bind(tag=TAG).info(
                    f"✅ 自动创建 UniApp 设备成功: {device_id}, "
                    f"设备名称: {result.get('device_name')}, "
                    f"绑定智能体: {result.get('agent_id')}"
                )
            elif result.get('exists'):
                self.logger.bind(tag=TAG).debug(
                    f"UniApp 设备已存在: {device_id}"
                )
            else:
                self.logger.bind(tag=TAG).warning(
                    f"UniApp 设备创建返回未知状态: {result}"
                )
                
        except ImportError:
            self.logger.bind(tag=TAG).error(
                "无法导入 create_uniapp_device_if_not_exists 函数，请检查 manage_api_client.py"
            )
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"自动创建 UniApp 设备失败: {e}")
            import traceback
            self.logger.bind(tag=TAG).debug(f"错误堆栈: {traceback.format_exc()}")

    def _get_user_persona(self, user_id: str):
        """
        获取“短期心情 + 长期画像”摘要

        Args:
            user_id: 用户ID

        Returns:
            str: 用户画像摘要文本,失败返回None
        """
        try:
            integration_conf = self.config.get("integration", {}) or {}
            base_url = integration_conf.get("unified_service_url") or "https://tag.zrj666.cn"
            client = UnifiedServiceClient(base_url=base_url)

            layered = client.build_layered_persona(user_id) or {}
            summary_text = str(layered.get("summary_text") or "").strip()
            if summary_text:
                self.logger.bind(tag=TAG).info(
                    f"✅ 成功获取用户 {user_id} 的分层画像（短期+长期）"
                )
                return summary_text

            # 兼容兜底：仍尝试旧画像摘要
            fallback_summary = client.get_user_persona(user_id)
            if fallback_summary:
                self.logger.bind(tag=TAG).info(
                    f"✅ 成功获取用户 {user_id} 的画像（fallback）"
                )
                return fallback_summary

            self.logger.bind(tag=TAG).debug(f"未获取到用户 {user_id} 的画像")
            return None
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"获取用户画像异常: {e}")
            return None


    def _get_music_signals(self, user_id: str) -> Dict[str, Any]:
        """Fetch weak-evidence music signals for dialogue policy."""
        if not user_id:
            return {}
        try:
            integration_conf = self.config.get("integration", {}) or {}
            base_url = integration_conf.get("unified_service_url") or "https://tag.zrj666.cn"
            client = UnifiedServiceClient(base_url=base_url)
            raw = client.build_music_signals(user_id) or {}
            if not isinstance(raw, dict):
                return {}

            # Whitelist and sanitize to keep payload small/safe.
            safe: Dict[str, Any] = {}
            if "source" in raw:
                safe["source"] = str(raw.get("source") or "")[:64]
            if "has_recent_music_activity" in raw:
                safe["has_recent_music_activity"] = bool(raw.get("has_recent_music_activity"))
            if "recent_play_count" in raw:
                try:
                    safe["recent_play_count"] = int(raw.get("recent_play_count") or 0)
                except Exception:
                    pass
            for key in ("low_arousal_ratio", "negative_ratio"):
                if key in raw and isinstance(raw.get(key), (int, float)):
                    safe[key] = float(raw.get(key))
            if "top_song_title" in raw:
                safe["top_song_title"] = str(raw.get("top_song_title") or "").strip()[:60]
            return safe
        except Exception as e:
            self.logger.bind(tag=TAG).debug(f"Fetch music signals failed: {e}")
            return {}

    def change_system_prompt(self, prompt):
        self.prompt = prompt
        # 更新系统prompt至上下文
        self.dialogue.update_system_message(self.prompt)

    def _emit_policy_event(self, stage: str, payload: Dict[str, Any]) -> None:
        """Emit policy-layer events to UniApp clients for progress visibility."""
        try:
            policy_conf = self.config.get("dialogue_policy", {}) or {}
            if not policy_conf.get("emit_events", True):
                self.logger.bind(tag=TAG).info(
                    f"policy_event skipped: emit_events disabled (stage={stage})"
                )
                return
            if not getattr(self, "is_uniapp_client", False):
                self.logger.bind(tag=TAG).info(
                    f"policy_event skipped: not uniapp client (stage={stage})"
                )
                return
            if not getattr(self, "websocket", None):
                self.logger.bind(tag=TAG).warning(
                    f"policy_event skipped: websocket missing (stage={stage})"
                )
                return

            message = {
                "type": "policy_event",
                "stage": stage,
                "payload": payload,
                "session_id": self.session_id,
            }
            if hasattr(self, "loop") and self.loop:
                self.logger.bind(tag=TAG).info(
                    f"policy_event emit: stage={stage}, payload_keys={list((payload or {}).keys())}"
                )
                future = asyncio.run_coroutine_threadsafe(
                    self.websocket.send(json.dumps(message, ensure_ascii=False)),
                    self.loop,
                )
                def _on_done(f):
                    try:
                        f.result()
                        self.logger.bind(tag=TAG).info(
                            f"policy_event sent: stage={stage}"
                        )
                    except Exception as send_error:
                        self.logger.bind(tag=TAG).warning(
                            f"policy_event send failed: stage={stage}, error={send_error}"
                        )
                future.add_done_callback(_on_done)
            else:
                self.logger.bind(tag=TAG).warning(
                    f"policy_event skipped: event loop unavailable (stage={stage})"
                )
        except Exception as e:
            self.logger.bind(tag=TAG).debug(f"Policy event send failed: {e}")

    def chat(self, query, depth=0):
        self.logger.bind(tag=TAG).info(f"大模型收到用户消息: {query}")
        
        # 在方法开始处导入，避免后续使用时出现 "referenced before assignment" 错误
        from core.providers.tts.dto.dto import ContentType, TTSMessageDTO, SentenceType
        
        # 检查LLM是否已初始化
        if self.llm is None:
            error_msg = "LLM未正确初始化，请检查配置文件中的LLM API密钥是否正确填写"
            self.logger.bind(tag=TAG).error(error_msg)
            # 发送错误提示给客户端
            if hasattr(self, 'tts') and self.tts is not None:
                self.sentence_id = str(uuid.uuid4().hex)
                self.tts.tts_text_queue.put(
                    TTSMessageDTO(
                        sentence_id=self.sentence_id,
                        sentence_type=SentenceType.FIRST,
                        content_type=ContentType.ACTION,
                    )
                )
                self.tts.tts_text_queue.put(
                    TTSMessageDTO(
                        sentence_id=self.sentence_id,
                        sentence_type=SentenceType.NORMAL,
                        content_type=ContentType.TEXT,
                        text="抱歉，系统配置有误，无法处理您的请求。请联系管理员检查LLM配置。",
                    )
                )
                self.tts.tts_text_queue.put(
                    TTSMessageDTO(
                        sentence_id=self.sentence_id,
                        sentence_type=SentenceType.END,
                        content_type=ContentType.ACTION,
                    )
                )
            return None
        
        self.llm_finish_task = False

        # 检查是否是 UniApp 客户端（不需要 TTS 音频处理）
        is_uniapp_client = getattr(self, 'is_uniapp_client', False)

        # 为最顶层时新建会话ID和发送FIRST请求
        if depth == 0:
            self.sentence_id = str(uuid.uuid4().hex)
            self.dialogue.put(Message(role="user", content=query))
            self._emit_policy_event(
                "policy_start",
                {"query": (query or "")[:80]},
            )
            # UniApp 客户端跳过 TTS 队列操作
            if not is_uniapp_client and hasattr(self, 'tts') and self.tts is not None:
                self.tts.tts_text_queue.put(
                    TTSMessageDTO(
                        sentence_id=self.sentence_id,
                        sentence_type=SentenceType.FIRST,
                        content_type=ContentType.ACTION,
                    )
                )

        # Define intent functions
        functions = None
        if self.intent_type == "function_call" and hasattr(self, "func_handler"):
            functions = self.func_handler.get_functions()
        response_message = []

        chat_start_ts = perf_counter()
        try:
            # 使用带记忆的对话
            memory_str = None
            original_dialogue = None
            memory_scope_key = self._ensure_memory_scope()
            if self.memory is not None:
                future = asyncio.run_coroutine_threadsafe(
                    self.memory.query_memory(query), self.loop
                )
                memory_str = future.result()

            # 🔥 用户画像注入逻辑 (仅对 UniApp 设备)
            persona_injected = False
            user_id = None
            memory_user_id = self.device_id
            persona_summary = ""
            episodic_store = None

            if self.device_id and self.device_id.startswith("uniapp_"):
                user_id = self.device_id[7:]  # 提取 user_id
                memory_user_id = user_id
                persona_summary = self._get_user_persona(user_id) or ""
                if persona_summary:
                    persona_injected = True
                    summary_preview = persona_summary.strip()
                    if len(summary_preview) > 200:
                        summary_preview = summary_preview[:200] + "..."
                    self._emit_policy_event(
                        "persona_injected",
                        {
                            "user_id": user_id,
                            "summary": summary_preview,
                        },
                    )
                    self.logger.bind(tag=TAG).info(f"✨ 已注入用户画像 (user_id={user_id})")

            # 记忆按 agent + 用户(或设备) 进行隔离，避免多智能体串用
            if memory_user_id:
                memory_scope_key = self._build_memory_scope_key()
            self.current_memory_scope_key = memory_scope_key

            # Dialogue policy + music signals (safe, optional)
            policy_conf = self.config.get("dialogue_policy", {}) or {}
            self.logger.bind(tag=TAG).info(
                f"runtime dialogue_policy={policy_conf}"
            )
            policy_prompt = ""
            try:
                from core.utils.dialogue_policy import build_dialogue_policy, render_policy_constraints
                from core.utils.prompt_blocks import PromptBlocks, PromptBlock, PromptAssembler
                from core.utils.state_manager import StateManager
                from core.utils.episodic_memory import EpisodicMemoryStore
                from core.utils.stable_memory import StableMemoryStore
                from core.utils.memory_packer import extract_long_term_items, pack
                from core.utils.personality_baseline import build_personality_baseline

                if policy_conf.get("enabled", True):
                    music_signals = {}
                    if policy_conf.get("enable_music_hypothesis", True) and user_id:
                        music_signals = self._get_music_signals(user_id)

                    policy = build_dialogue_policy(
                        query=query,
                        dialogue=self.dialogue.dialogue,
                        memory_str=memory_str or "",
                        config=self.config,
                        stable_memory_items=None,
                        episodic_memory_items=None,
                        last_intent=getattr(self, "last_intent", None),
                        music_signals=music_signals,
                        policy_state=getattr(self, "dialogue_policy_state", {}),
                        current_user_text=query,
                        entry_context=getattr(self, "entry_context", {}),
                    )
                    self.last_policy = dict(policy or {})
                    self.dialogue_policy_state = policy.get("next_policy_state", {})
                    self._emit_policy_event(
                        "policy_decision",
                        {
                            "companionship_need": policy.get("companionship_need"),
                            "intent": policy.get("intent"),
                            "intent_label": policy.get("intent_label"),
                            "tone": policy.get("tone"),
                            "length": policy.get("length"),
                            "question_density": policy.get("question_density"),
                            "initiative": policy.get("initiative"),
                            "suggestion_strength": policy.get("suggestion_strength"),
                            "risk_level": policy.get("risk_level"),
                            "micro_skill_id": policy.get("micro_skill_id"),
                            "micro_skill_text": policy.get("micro_skill_text"),
                            "memory_to_inject": policy.get("memory_to_inject", []),
                            "music_role": policy.get("music_role"),
                            "music_hypothesis": policy.get("music_hypothesis"),
                            "music_soft_confirmation": policy.get("music_soft_confirmation"),
                            "need_confirmation": policy.get("need_confirmation"),
                            "confirmation_status": policy.get("confirmation_status"),
                            "ui_state_hint": derive_ui_state_hint(
                                policy.get("companionship_need"),
                                phase="policy",
                            ),
                            "entry_context": getattr(self, "entry_context", {}),
                        },
                    )
                    policy_prompt = render_policy_constraints(policy).strip()
                    self.last_intent = policy.get("intent")
                else:
                    self.logger.bind(tag=TAG).info(
                        "policy_event skipped: dialogue_policy disabled (stage=policy_decision)"
                    )

                # Build blocks
                base_system_prompt = self._append_mimo_tts_prompt_rules(self.prompt or "")
                policy_block = PromptBlock(
                    text=policy_prompt,
                    usage_hint="必须遵守以下策略。",
                    enabled=bool(policy_prompt),
                )
                personality_prompt = build_personality_baseline(self.config)
                personality_block = PromptBlock(
                    text=personality_prompt,
                    usage_hint="这是助手自身的长期表达基线，不是用户画像；不得覆盖本轮安全策略。",
                    enabled=bool(personality_prompt),
                )
                persona_block = PromptBlock(
                    text=persona_summary,
                    usage_hint="仅用于长期偏好和风格参考，不得推断当前情绪结论。",
                    enabled=bool(persona_summary),
                )
                vision_context = getattr(self, "pending_visual_context", None) or {}
                vision_text = ""
                if vision_context:
                    entities = vision_context.get("vision_entities") or []
                    if isinstance(entities, list):
                        entities_text = "、".join([str(item) for item in entities[:3] if str(item).strip()])
                    else:
                        entities_text = str(entities)
                    vision_lines = []
                    summary = str(vision_context.get("vision_summary") or "").strip()
                    if summary:
                        vision_lines.append(f"图片概述：{summary}")
                    if entities_text:
                        vision_lines.append(f"主要元素：{entities_text}")
                    hint = str(vision_context.get("vision_emotion_hint") or "").strip()
                    if hint:
                        vision_lines.append(f"氛围线索：{hint}")
                    source = str(vision_context.get("source") or "").strip()
                    if source:
                        vision_lines.append(f"来源：{source}")
                    vision_text = "\n".join(vision_lines).strip()
                vision_block = PromptBlock(
                    text=vision_text,
                    usage_hint="这是用户刚分享图片的弱视觉上下文，不得当作确定事实，需结合用户文字自然回应。",
                    enabled=bool(vision_text),
                )
                self.pending_visual_context = None
                state_text = StateManager.export_for_prompt(
                    getattr(self, "dialogue_policy_state", {}), self.last_intent
                )
                state_block = PromptBlock(
                    text=state_text,
                    usage_hint="优先处理待确认事项与未闭环。",
                    enabled=bool(state_text),
                )

                stable_items = []
                episodic_items = []
                long_term_items = []
                if memory_scope_key:
                    stable_store = StableMemoryStore()
                    episodic_store = EpisodicMemoryStore()
                    try:
                        if policy_conf.get("enable_stable_memory", True):
                            stable_store.update_from_user_text(memory_scope_key, query)
                            stable_items = stable_store.query_relevant(
                                memory_scope_key, query, limit=2
                            )
                    except Exception as e:
                        self.logger.bind(tag=TAG).debug(
                            f"stable memory fetch failed: {e}"
                        )
                    try:
                        episodic_store.observe_user_input(memory_scope_key, query)
                        episodic_items = episodic_store.query_relevant_struct(
                            memory_scope_key, query, limit=3
                        )
                    except Exception as e:
                        self.logger.bind(tag=TAG).debug(
                            f"episodic memory fetch failed: {e}"
                        )

                if memory_str:
                    try:
                        long_term_items = extract_long_term_items(
                            memory_str, query, limit=3
                        )
                    except Exception as e:
                        self.logger.bind(tag=TAG).debug(
                            f"long_term memory extract failed: {e}"
                        )

                memory_items = []
                for item in stable_items:
                    memory_items.append(
                        {"source": "stable", "type": "preference", "text": item}
                    )
                memory_items.extend(episodic_items)
                memory_items.extend(long_term_items)
                memory_text, memory_stats = pack(
                    memory_items, current_user_text=query, max_items=3
                )
                memory_block = PromptBlock(
                    text=memory_text,
                    usage_hint="可引用事实，冲突需澄清。",
                    enabled=bool(memory_text),
                )

                blocks = PromptBlocks(
                    policy_block=policy_block,
                    personality_block=personality_block,
                    persona_block=persona_block,
                    vision_block=vision_block,
                    state_block=state_block,
                    memory_block=memory_block,
                )
                final_system_prompt, block_stats = PromptAssembler.render(
                    base_system_prompt, blocks
                )
                self.dialogue.update_system_message(final_system_prompt)

                # logging stats
                policy_len = len(policy_block.render())
                personality_len = len(personality_block.render())
                persona_len = len(persona_block.render())
                vision_len = len(vision_block.render())
                state_len = len(state_block.render())
                memory_len = len(memory_block.render())
                system_prompt_len = len(final_system_prompt or "")
                self.logger.bind(tag=TAG).info(
                    f"prompt_blocks len: policy={policy_len} personality={personality_len} persona={persona_len} vision={vision_len} state={state_len} memory={memory_len} system={system_prompt_len}"
                )
                self.logger.bind(tag=TAG).info(
                    f"memory_packer stats: {memory_stats}"
                )
                self.logger.bind(tag=TAG).info(
                    f"prompt_blocks inject: {block_stats}"
                )
                # Emit block usage for frontend visibility.
                self._emit_policy_event(
                    "prompt_blocks",
                    {
                        "blocks": {
                            "policy": {
                                "enabled": bool(policy_block.enabled and policy_block.text),
                                "length": len(policy_block.render()),
                                "inject_mode": block_stats.get("policy"),
                                "preview": policy_block.render()[:160],
                            },
                            "personality": {
                                "enabled": bool(personality_block.enabled and personality_block.text),
                                "length": len(personality_block.render()),
                                "inject_mode": block_stats.get("personality"),
                                "preview": personality_block.render()[:160],
                            },
                            "persona": {
                                "enabled": bool(persona_block.enabled and persona_block.text),
                                "length": len(persona_block.render()),
                                "inject_mode": block_stats.get("persona"),
                                "preview": persona_block.render()[:160],
                            },
                            "vision": {
                                "enabled": bool(vision_block.enabled and vision_block.text),
                                "length": len(vision_block.render()),
                                "inject_mode": block_stats.get("vision"),
                                "preview": vision_block.render()[:160],
                            },
                            "state": {
                                "enabled": bool(state_block.enabled and state_block.text),
                                "length": len(state_block.render()),
                                "inject_mode": block_stats.get("state"),
                                "preview": state_block.render()[:160],
                            },
                            "memory": {
                                "enabled": bool(memory_block.enabled and memory_block.text),
                                "length": len(memory_block.render()),
                                "inject_mode": block_stats.get("memory"),
                                "preview": memory_block.render()[:160],
                            },
                        },
                        "memory_items": {
                            "items_in": memory_stats.get("items_in", 0),
                            "items_out": memory_stats.get("items_out", 0),
                            "dedup_hard": memory_stats.get("dedup_hard", 0),
                            "dedup_nearby": memory_stats.get("dedup_nearby", 0),
                            "dropped_current": memory_stats.get("dropped_current", 0),
                        },
                    },
                )
            except Exception as e:
                self.logger.bind(tag=TAG).warning(f"Dialogue policy build failed: {e}")

            llm_dialogue_payload = self.dialogue.get_llm_dialogue_with_memory(
                None, None, self.config.get("voiceprint", {})
            )
            llm_dialogue_chars = len(
                json.dumps(llm_dialogue_payload, ensure_ascii=False)
            )
            llm_model = getattr(
                self.llm,
                "model_name",
                getattr(self.llm, "model", self.llm.__class__.__name__),
            )
            llm_stream_start_ts = perf_counter()
            self.logger.bind(tag=TAG).info(
                f"llm request start: model={llm_model}, intent_type={self.intent_type}, depth={depth}, dialogue_chars={llm_dialogue_chars}, chat_elapsed={llm_stream_start_ts - chat_start_ts:.3f}s"
            )

            if self.intent_type == "function_call" and functions is not None:
                # 使用支持functions的streaming接口
                llm_responses = self.llm.response_with_functions(
                    self.session_id,
                    llm_dialogue_payload,
                    functions=functions,
                )
            else:
                llm_responses = self.llm.response(
                    self.session_id,
                    llm_dialogue_payload,
                )
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"LLM 处理出错 {query}: {e}")
            return None

        # 处理流式响应
        tool_call_flag = False
        function_name = None
        function_id = None
        function_arguments = ""
        content_arguments = ""
        self.client_abort = False
        emotion_flag = True
        llm_first_chunk_ts = None
        llm_chunk_count = 0
        llm_text_chars = 0
        sentence_buffer = ""
        reply_turn = self._create_reply_turn() if is_uniapp_client else None
        if reply_turn:
            asyncio.run_coroutine_threadsafe(
                self._dispatch_sentence_audio_in_order(reply_turn),
                self.loop,
            )
        for response in llm_responses:
            if self.client_abort:
                if reply_turn:
                    reply_turn.aborted = True
                break
            if self.intent_type == "function_call" and functions is not None:
                content, tools_call = response
                if "content" in response:
                    content = response["content"]
                    tools_call = None
                if content is not None and len(content) > 0:
                    content_arguments += content

                if not tool_call_flag and content_arguments.startswith("<tool_call>"):
                    # print("content_arguments", content_arguments)
                    tool_call_flag = True

                if tools_call is not None and len(tools_call) > 0:
                    tool_call_flag = True
                    if tools_call[0].id is not None:
                        function_id = tools_call[0].id
                    if tools_call[0].function.name is not None:
                        function_name = tools_call[0].function.name
                    if tools_call[0].function.arguments is not None:
                        function_arguments += tools_call[0].function.arguments
            else:
                content = response

            llm_chunk_count += 1
            if llm_first_chunk_ts is None:
                llm_first_chunk_ts = perf_counter()
                self.logger.bind(tag=TAG).info(
                    f"llm first chunk: latency={llm_first_chunk_ts - llm_stream_start_ts:.3f}s, chunk_index={llm_chunk_count}"
                )

            # 在llm回复中获取情绪表情，一轮对话只在开头获取一次
            if emotion_flag and content is not None and content.strip():
                asyncio.run_coroutine_threadsafe(
                    textUtils.get_emotion(self, content),
                    self.loop,
                )
                emotion_flag = False

            if content is not None and len(content) > 0:
                llm_text_chars += len(content)
                if not tool_call_flag:
                    response_message.append(content)
                    if reply_turn:
                        sentence_buffer += content
                        pending_count = self._pending_sentence_count(reply_turn)
                        backpressure_active = (
                            pending_count
                            >= self._sentence_pipeline_config["max_pending_sentences"]
                        )
                        sentences, sentence_buffer = self._build_sentences_from_buffer(
                            sentence_buffer,
                            force_flush=False,
                            backpressure_active=backpressure_active,
                        )
                        for item in sentences:
                            sentence = self._register_sentence_unit(reply_turn, item)
                            self._send_sentence_text_chunk(reply_turn, sentence)
                            self._schedule_sentence_audio(reply_turn, sentence)
                    # UniApp 客户端跳过 TTS 队列操作
                    if not is_uniapp_client and hasattr(self, 'tts') and self.tts is not None:
                        self.tts.tts_text_queue.put(
                            TTSMessageDTO(
                                sentence_id=self.sentence_id,
                                sentence_type=SentenceType.MIDDLE,
                                content_type=ContentType.TEXT,
                                content_detail=content,
                            )
                        )
        # 处理function call
        if tool_call_flag:
            bHasError = False
            if function_id is None:
                a = extract_json_from_string(content_arguments)
                if a is not None:
                    try:
                        content_arguments_json = json.loads(a)
                        function_name = content_arguments_json["name"]
                        function_arguments = json.dumps(
                            content_arguments_json["arguments"], ensure_ascii=False
                        )
                        function_id = str(uuid.uuid4().hex)
                    except Exception as e:
                        bHasError = True
                        response_message.append(a)
                else:
                    bHasError = True
                    response_message.append(content_arguments)
                if bHasError:
                    self.logger.bind(tag=TAG).error(
                        f"function call error: {content_arguments}"
                    )
            if not bHasError:
                # 如需要大模型先处理一轮，添加相关处理后的日志情况
                if len(response_message) > 0:
                    text_buff = "".join(response_message)
                    self.tts_MessageText = text_buff
                    self.dialogue.put(Message(role="assistant", content=text_buff))
                response_message.clear()
                self.logger.bind(tag=TAG).debug(
                    f"function_name={function_name}, function_id={function_id}, function_arguments={function_arguments}"
                )
                function_call_data = {
                    "name": function_name,
                    "id": function_id,
                    "arguments": function_arguments,
                }

                # 使用统一工具处理器处理所有工具调用
                result = asyncio.run_coroutine_threadsafe(
                    self.func_handler.handle_llm_function_call(
                        self, function_call_data
                    ),
                    self.loop,
                ).result()
                if reply_turn:
                    reply_turn.llm_completed = True
                    self._wait_reply_turn_completion(reply_turn)
                self._handle_function_result(result, function_call_data, depth=depth)

        # 存储对话内容
        if len(response_message) > 0:
            text_buff = "".join(response_message)
            self.tts_MessageText = text_buff
            self.dialogue.put(Message(role="assistant", content=text_buff))
            self._update_episodic_memory(
                memory_scope_key, query, text_buff, episodic_store
            )

            # 为 UniApp 客户端发送 WebSocket 回复
            if is_uniapp_client and reply_turn:
                final_sentences, sentence_buffer = self._build_sentences_from_buffer(
                    sentence_buffer,
                    force_flush=True,
                    backpressure_active=False,
                )
                for idx, item in enumerate(final_sentences):
                    sentence = self._register_sentence_unit(
                        reply_turn,
                        item,
                        is_final=(idx == len(final_sentences) - 1),
                    )
                    self._send_sentence_text_chunk(reply_turn, sentence)
                    self._schedule_sentence_audio(reply_turn, sentence)
                reply_turn.llm_completed = True
                self._wait_reply_turn_completion(reply_turn)
        elif reply_turn:
            reply_turn.llm_completed = True
            if sentence_buffer.strip():
                final_sentences, sentence_buffer = self._build_sentences_from_buffer(
                    sentence_buffer,
                    force_flush=True,
                    backpressure_active=False,
                )
                for idx, item in enumerate(final_sentences):
                    sentence = self._register_sentence_unit(
                        reply_turn,
                        item,
                        is_final=(idx == len(final_sentences) - 1),
                    )
                    self._send_sentence_text_chunk(reply_turn, sentence)
                    self._schedule_sentence_audio(reply_turn, sentence)
            self._wait_reply_turn_completion(reply_turn)
        
        if depth == 0:
            # UniApp 客户端跳过 TTS 队列操作
            if not is_uniapp_client and hasattr(self, 'tts') and self.tts is not None:
                self.tts.tts_text_queue.put(
                    TTSMessageDTO(
                        sentence_id=self.sentence_id,
                        sentence_type=SentenceType.LAST,
                        content_type=ContentType.ACTION,
                    )
                )
        
        # 🔥 恢复原始对话 (如果注入了画像)
        if persona_injected and original_dialogue is not None:
            self.dialogue.dialogue = original_dialogue
            self.logger.bind(tag=TAG).debug("已恢复原始对话上下文")

        llm_end_ts = perf_counter()
        first_chunk_latency = (
            llm_first_chunk_ts - llm_stream_start_ts
            if llm_first_chunk_ts is not None
            else None
        )
        self.logger.bind(tag=TAG).info(
            f"llm stream done: model={llm_model}, chunks={llm_chunk_count}, text_chars={llm_text_chars}, first_chunk_latency={first_chunk_latency:.3f}s total_llm_time={llm_end_ts - llm_stream_start_ts:.3f}s total_chat_time={llm_end_ts - chat_start_ts:.3f}s tool_call={tool_call_flag}"
            if first_chunk_latency is not None
            else f"llm stream done: model={llm_model}, chunks={llm_chunk_count}, text_chars={llm_text_chars}, first_chunk_latency=none total_llm_time={llm_end_ts - llm_stream_start_ts:.3f}s total_chat_time={llm_end_ts - chat_start_ts:.3f}s tool_call={tool_call_flag}"
        )

        self.llm_finish_task = True
        # 使用lambda延迟计算，只有在DEBUG级别时才执行get_llm_dialogue()
        self.logger.bind(tag=TAG).debug(
            lambda: json.dumps(
                self.dialogue.get_llm_dialogue(), indent=4, ensure_ascii=False
            )
        )

        return True

    def _update_episodic_memory(
        self,
        memory_scope_key: str | None,
        user_text: str,
        assistant_text: str,
        episodic_store=None,
    ) -> None:
        if not memory_scope_key or not user_text or not assistant_text:
            return
        try:
            if episodic_store is None:
                from core.utils.episodic_memory import EpisodicMemoryStore

                episodic_store = EpisodicMemoryStore()
            store = episodic_store
            store.update_from_turn(
                memory_scope_key,
                user_text,
                assistant_text,
                context=build_episodic_turn_context(
                    getattr(self, "entry_context", {}),
                    getattr(self, "last_policy", {}).get("companionship_need"),
                ),
            )
        except Exception as e:
            self.logger.bind(tag=TAG).debug(f"episodic memory write failed: {e}")

    def _handle_function_result(self, result, function_call_data, depth):
        if result.action == Action.RESPONSE:  # 直接回复前端
            text = result.response
            if getattr(self, "is_uniapp_client", False):
                self._dispatch_uniapp_response(text)
            else:
                self.tts.tts_one_sentence(self, ContentType.TEXT, content_detail=text)
            self.dialogue.put(Message(role="assistant", content=text))
            self._update_episodic_memory(
                getattr(self, "current_memory_scope_key", None),
                getattr(self, "last_user_message", "") or "",
                text,
            )
        elif result.action == Action.REQLLM:  # 调用函数后再请求llm生成回复
            text = result.result
            if text is not None and len(text) > 0:
                function_id = function_call_data["id"]
                function_name = function_call_data["name"]
                function_arguments = function_call_data["arguments"]
                self.dialogue.put(
                    Message(
                        role="assistant",
                        tool_calls=[
                            {
                                "id": function_id,
                                "function": {
                                    "arguments": (
                                        "{}"
                                        if function_arguments == ""
                                        else function_arguments
                                    ),
                                    "name": function_name,
                                },
                                "type": "function",
                                "index": 0,
                            }
                        ],
                    )
                )

                self.dialogue.put(
                    Message(
                        role="tool",
                        tool_call_id=(
                            str(uuid.uuid4()) if function_id is None else function_id
                        ),
                        content=text,
                    )
                )
                self.chat(text, depth=depth + 1)
        elif result.action == Action.NOTFOUND or result.action == Action.ERROR:
            text = result.response if result.response else result.result
            if getattr(self, "is_uniapp_client", False):
                self._dispatch_uniapp_response(text)
            else:
                self.tts.tts_one_sentence(self, ContentType.TEXT, content_detail=text)
            self.dialogue.put(Message(role="assistant", content=text))
            self._update_episodic_memory(
                getattr(self, "current_memory_scope_key", None),
                getattr(self, "last_user_message", "") or "",
                text,
            )
        else:
            pass

    def _build_uniapp_speech_plan(self, text: str, turn: ReplyTurn | None = None) -> Dict[str, str]:
        companion_conf = self.config.get("companion", {}) or {}
        return build_speech_plan(
            text,
            user_text=getattr(self, "last_user_message", ""),
            agent_style=companion_conf.get("speaking_style"),
            turn_style=turn.turn_style if turn else None,
        )

    def _should_use_mimo_prosody_rewrite(self) -> bool:
        if self.llm is None or self.tts is None:
            return False
        selected_tts = (self.config.get("selected_module", {}) or {}).get("TTS")
        tts_conf = (self.config.get("TTS", {}) or {}).get(selected_tts, {})
        return (
            (tts_conf.get("type") or "").lower() == "xiaomimimo"
            and self._sentence_pipeline_config.get("max_rewrite_sentences", 0) > 0
        )

    def _is_mimo_tts(self) -> bool:
        selected_tts = (self.config.get("selected_module", {}) or {}).get("TTS")
        tts_conf = (self.config.get("TTS", {}) or {}).get(selected_tts, {})
        return (tts_conf.get("type") or "").lower() == "xiaomimimo"

    def _append_mimo_tts_prompt_rules(self, prompt: str) -> str:
        if not self._is_mimo_tts():
            return prompt
        if MIMO_TTS_PROMPT_RULES in (prompt or ""):
            return prompt
        return (prompt or "").rstrip() + "\n\n[MiMo语音合成规则]\n" + MIMO_TTS_PROMPT_RULES + "\n"

    def _rewrite_sentence_for_mimo(
        self,
        turn: ReplyTurn,
        sentence_unit: SentenceUnit,
        speech_plan: Dict[str, str],
    ) -> tuple[Dict[str, str], bool]:
        if not self._should_use_mimo_prosody_rewrite():
            self.logger.bind(tag=TAG).info(
                f"sentence_rewrite_skipped: idx={sentence_unit.sentence_index} reason=tts_not_mimo"
            )
            return speech_plan, False
        if sentence_unit.sentence_index >= self._sentence_pipeline_config["max_rewrite_sentences"]:
            self.logger.bind(tag=TAG).info(
                f"sentence_rewrite_skipped: idx={sentence_unit.sentence_index} reason=sentence_limit"
            )
            return speech_plan, False
        if turn.rewrite_disabled:
            self.logger.bind(tag=TAG).info(
                f"sentence_rewrite_skipped: idx={sentence_unit.sentence_index} reason=turn_fused"
            )
            return speech_plan, False

        display_text = (speech_plan.get("display_text") or "").strip()
        if not display_text:
            return speech_plan, False
        if len(re.sub(r"\s+", "", display_text)) < self._sentence_pipeline_config["rewrite_min_chars"]:
            self.logger.bind(tag=TAG).info(
                f"sentence_rewrite_skipped: idx={sentence_unit.sentence_index} reason=too_short"
            )
            return speech_plan, False

        companion_conf = self.config.get("companion", {}) or {}
        system_prompt = build_mimo_prosody_system_prompt()
        user_prompt = build_mimo_prosody_user_prompt(
            display_text,
            user_text=getattr(self, "last_user_message", "") or "",
            speech_mode=getattr(self, "last_intent", "") or "",
            risk_level=(getattr(self, "dialogue_policy_state", {}) or {}).get(
                "risk_level", ""
            ),
            agent_style=companion_conf.get("speaking_style"),
        )
        def _run_rewrite():
            return self.llm.response_no_stream(
                system_prompt,
                user_prompt,
                max_tokens=96,
                temperature=0.1,
            )

        temp_executor = ThreadPoolExecutor(max_workers=1)
        fallback_used = False
        try:
            with self._rewrite_slots:
                self.logger.bind(tag=TAG).info(
                    f"sentence_rewrite_start: idx={sentence_unit.sentence_index}"
                )
                future = temp_executor.submit(_run_rewrite)
                raw_text = future.result(
                    timeout=self._sentence_pipeline_config.get("rewrite_timeout", 2.5)
                )
            if is_invalid_mimo_rewrite(raw_text, display_text):
                self.logger.bind(tag=TAG).warning(
                    f"sentence_rewrite_fallback: idx={sentence_unit.sentence_index} reason=invalid_output"
                )
                fallback_used = True
                return speech_plan, True
            assistant_text = extract_mimo_tts_text(
                raw_text,
                display_text,
                fallback_style=speech_plan.get("tts_style") or DEFAULT_STYLE,
            )
            speech_plan["assistant_text"] = assistant_text
            speech_plan["tts_text"] = assistant_text
            speech_plan["speak_text"] = assistant_text
            self.logger.bind(tag=TAG).info(
                f"sentence_rewrite_done: idx={sentence_unit.sentence_index} preview={assistant_text[:80]}"
            )
            return speech_plan, False
        except Exception as e:
            error_text = str(e)
            if "timeout" in error_text.lower():
                self.logger.bind(tag=TAG).warning(
                    f"sentence_rewrite_timeout: idx={sentence_unit.sentence_index}"
                )
            else:
                self.logger.bind(tag=TAG).warning(
                    f"sentence_rewrite_fallback: idx={sentence_unit.sentence_index} error={e}"
                )
            fallback_used = True
            return speech_plan, True
        finally:
            temp_executor.shutdown(wait=False)
            if fallback_used:
                turn.rewrite_failures += 1
                if turn.rewrite_failures >= 2:
                    turn.rewrite_disabled = True

    async def _send_uniapp_event(self, payload: Dict[str, Any]) -> None:
        if not getattr(self, "websocket", None):
            return
        await self.websocket.send(json.dumps(payload, ensure_ascii=False))

    def _send_uniapp_event_sync(self, payload: Dict[str, Any]) -> None:
        if not getattr(self, "websocket", None):
            return
        asyncio.run_coroutine_threadsafe(
            self._send_uniapp_event(payload),
            self.loop,
        ).result()

    def _create_reply_turn(self) -> ReplyTurn:
        mode = "immersive" if self._is_immersive_mode() else "normal"
        turn = ReplyTurn(
            message_id=str(uuid.uuid4().hex),
            session_id=self.session_id,
            mode=mode,
            send_text_chunks=(mode == "normal"),
        )
        with self.reply_turn_lock:
            self.current_reply_turn = turn
            self._active_reply_message_ids.add(turn.message_id)
        return turn

    def _build_sentence_reply_meta(self, speech_plan: Dict[str, Any] | None = None) -> Dict[str, Any]:
        return build_reply_meta(
            getattr(self, "last_policy", {}).get("companionship_need"),
            speech_plan,
            phase="response",
        )

    def _emit_reply_start_if_needed(self, turn: ReplyTurn) -> None:
        if turn.reply_started:
            return
        payload = {
            "type": "reply_start",
            "message_id": turn.message_id,
            "session_id": turn.session_id,
            "mode": turn.mode,
            "reply_meta": self._build_sentence_reply_meta(),
        }
        self._send_uniapp_event_sync(payload)
        turn.reply_started = True

    async def _emit_reply_start_if_needed_async(self, turn: ReplyTurn) -> None:
        if turn.reply_started:
            return
        payload = {
            "type": "reply_start",
            "message_id": turn.message_id,
            "session_id": turn.session_id,
            "mode": turn.mode,
            "reply_meta": self._build_sentence_reply_meta(),
        }
        await self._send_uniapp_event(payload)
        turn.reply_started = True

    def _register_sentence_unit(
        self, turn: ReplyTurn, display_text: str, is_final: bool = False
    ) -> SentenceUnit:
        raw_text = display_text
        detected_style = extract_mimo_style(raw_text)
        if detected_style and not turn.turn_style:
            turn.turn_style = detected_style
        speech_plan = self._build_uniapp_speech_plan(raw_text, turn)
        if speech_plan.get("tts_style") and not turn.turn_style:
            turn.turn_style = speech_plan["tts_style"]
            speech_plan = self._build_uniapp_speech_plan(raw_text, turn)
        display_text = speech_plan.get("display_text", raw_text)
        sentence = SentenceUnit(
            message_id=turn.message_id,
            sentence_index=turn.next_sentence_index,
            display_text=display_text,
            speak_text=speech_plan.get("assistant_text")
            or speech_plan.get("tts_text")
            or display_text,
            is_final=is_final,
            reply_meta=self._build_sentence_reply_meta(speech_plan),
            speech_plan=speech_plan,
        )
        turn.sentences[sentence.sentence_index] = sentence
        turn.next_sentence_index += 1
        turn.full_display_text += sentence.display_text
        return sentence

    def _send_sentence_text_chunk(self, turn: ReplyTurn, sentence: SentenceUnit) -> None:
        if not turn.send_text_chunks or sentence.text_sent:
            return
        payload = {
            "type": "reply_text_chunk",
            "message_id": turn.message_id,
            "session_id": turn.session_id,
            "sentence_index": sentence.sentence_index,
            "text": sentence.display_text,
            "is_final_sentence": bool(sentence.is_final and turn.llm_completed),
            "reply_meta": sentence.reply_meta,
        }
        self._emit_reply_start_if_needed(turn)
        self._send_uniapp_event_sync(payload)
        sentence.text_sent = True

    def _build_sentence_audio_path(
        self,
        turn: ReplyTurn,
        sentence: SentenceUnit,
        audio_ext_override: str | None = None,
    ) -> tuple[str, str, str]:
        audio_dir = self.config.get("uniapp_audio_dir", "data/uniapp_audio")
        audio_base_url = self.config.get(
            "uniapp_audio_base_url", "http://182.254.228.131:8013/audio"
        ).rstrip("/")
        audio_ext = audio_ext_override or getattr(self.tts, "audio_file_type", "mp3") or "mp3"
        message_dir = os.path.join(audio_dir, turn.session_id, turn.message_id)
        os.makedirs(message_dir, exist_ok=True)
        filename = f"{sentence.sentence_index:03d}_{int(time.time() * 1000)}.{audio_ext}"
        file_path = os.path.join(message_dir, filename)
        relative = f"{turn.session_id}/{turn.message_id}/{filename}".replace("\\", "/")
        return file_path, f"{audio_base_url}/{relative}", audio_ext

    def _generate_sentence_audio(self, turn: ReplyTurn, sentence: SentenceUnit) -> None:
        if self.client_abort or turn.aborted or self.tts is None:
            sentence.audio_skipped = True
            sentence.status = "skipped"
            turn.audio_terminal_count += 1
            return
        pending_audio_jobs = len(
            [
                item
                for item in turn.sentences.values()
                if item.status in {"queued", "rewriting", "tts"}
            ]
        )
        if (
            turn.mode == "immersive"
            and pending_audio_jobs >= self._sentence_pipeline_config["max_pending_audio_jobs"]
            and sentence.sentence_index >= 2
            and not turn.first_audio_sent
        ):
            sentence.audio_skipped = True
            sentence.status = "skipped"
            turn.audio_terminal_count += 1
            return

        sentence.status = "tts"
        speech_plan = dict(sentence.speech_plan or {})
        speech_plan["display_text"] = sentence.display_text
        speech_plan["speak_text"] = sentence.speak_text
        speak_text = (
            speech_plan.get("speak_text")
            or speech_plan.get("assistant_text")
            or speech_plan.get("tts_text")
            or sentence.display_text
        )
        sentence.speak_text = speak_text
        sentence.speech_plan = speech_plan
        sentence.reply_meta = self._build_sentence_reply_meta(speech_plan)
        use_streaming_tts = self._is_mimo_tts() and hasattr(self.tts, "stream_text_to_speak")
        file_path, audio_url, audio_ext = (
            self._build_sentence_audio_path(
                turn,
                sentence,
                audio_ext_override="wav" if use_streaming_tts else None,
            )
        )
        sentence.audio_path = file_path
        if file_path:
            self._pending_sentence_audio_files.add(file_path)
        self.logger.bind(tag=TAG).info(
            f"sentence_tts_start: idx={sentence.sentence_index} stream={use_streaming_tts}"
        )
        try:
            with self._tts_slots:
                if use_streaming_tts:
                    chunk_count = 0
                    pcm_chunks = []
                    sentence.audio_format = "wav"
                    sentence.status = "tts"
                    for audio_base64 in self.tts.stream_text_to_speak(speak_text):
                        if self.client_abort or turn.aborted:
                            sentence.audio_skipped = True
                            sentence.status = "skipped"
                            sentence.audio_failure_reason = "aborted"
                            return
                        pcm_chunks.append(audio_base64)
                        sentence.audio_chunk_count += 1
                        chunk_count += 1
                    if chunk_count <= 0:
                        raise TTSException("xiaomimimo TTS stream returned no audio chunks")
                    wav_bytes = pcm16_base64_chunks_to_wav_bytes(
                        pcm_chunks,
                        sample_rate=24000,
                        channels=1,
                    )
                    sentence.audio_duration_ms = pcm16_base64_chunks_duration_ms(
                        pcm_chunks,
                        sample_rate=24000,
                        channels=1,
                    )
                    with open(file_path, "wb") as audio_file:
                        audio_file.write(wav_bytes)
                    sentence.audio_url = audio_url
                    sentence.audio_base64 = None
                    sentence.audio_stream_done = True
                    sentence.audio_ready_timestamp_ms = int(time.time() * 1000)
                    sentence.status = "audio_ready"
                else:
                    asyncio.run(self.tts.text_to_speak(speak_text, file_path))
            if self.client_abort or turn.aborted:
                sentence.audio_skipped = True
                sentence.status = "skipped"
                sentence.audio_failure_reason = "aborted"
                if file_path and os.path.exists(file_path):
                    os.remove(file_path)
                turn.audio_terminal_count += 1
                if file_path:
                    self._pending_sentence_audio_files.discard(file_path)
                return
            if not use_streaming_tts:
                sentence.audio_url = audio_url
                sentence.audio_format = audio_ext
                sentence.audio_ready_timestamp_ms = int(time.time() * 1000)
                sentence.status = "audio_ready"
            self.logger.bind(tag=TAG).info(
                f"sentence_tts_end: idx={sentence.sentence_index} path={file_path} chunks={sentence.audio_chunk_count}"
            )
        except Exception as e:
            sentence.audio_failed = True
            sentence.audio_skipped = True
            sentence.status = "audio_failed"
            sentence.audio_failure_reason = str(e)
            self.logger.bind(tag=TAG).warning(
                f"sentence_tts_failed: idx={sentence.sentence_index} error={e}"
            )
            if file_path and os.path.exists(file_path):
                os.remove(file_path)
            if file_path:
                self._pending_sentence_audio_files.discard(file_path)
        finally:
            if sentence.status in {"audio_failed", "skipped"}:
                turn.audio_terminal_count += 1

    async def _dispatch_sentence_audio_in_order(self, turn: ReplyTurn) -> None:
        while not turn.audio_dispatch_done:
            if turn.aborted and turn.audio_terminal_count >= len(turn.sentences):
                break
            sentence = turn.sentences.get(turn.next_audio_dispatch_index)
            if sentence is None:
                if turn.llm_completed and turn.next_audio_dispatch_index >= len(turn.sentences):
                    break
                await asyncio.sleep(0.05)
                continue

            if sentence.status in {"audio_streaming", "audio_stream_done", "audio_failed"}:
                while not sentence.audio_chunks.empty():
                    audio_base64 = sentence.audio_chunks.get_nowait()
                    await self._emit_reply_start_if_needed_async(turn)
                    if not sentence.audio_started_sent:
                        await self._send_uniapp_event(
                            {
                                "type": "reply_audio_start",
                                "message_id": turn.message_id,
                                "session_id": turn.session_id,
                                "sentence_index": sentence.sentence_index,
                                "audio_format": "pcm16",
                                "sample_rate": 24000,
                                "channels": 1,
                                "reply_meta": sentence.reply_meta,
                            }
                        )
                        sentence.audio_started_sent = True
                    await self._send_uniapp_event(
                        {
                            "type": "reply_audio_delta",
                            "message_id": turn.message_id,
                            "session_id": turn.session_id,
                            "sentence_index": sentence.sentence_index,
                            "audio_base64": audio_base64,
                            "reply_meta": sentence.reply_meta,
                        }
                    )
                    if not turn.first_audio_sent:
                        self.logger.bind(tag=TAG).info(
                            f"first_audio_sent: idx={sentence.sentence_index}"
                        )
                        turn.first_audio_sent = True

                if sentence.status == "audio_stream_done":
                    await self._emit_reply_start_if_needed_async(turn)
                    if not sentence.audio_started_sent:
                        await self._send_uniapp_event(
                            {
                                "type": "reply_audio_start",
                                "message_id": turn.message_id,
                                "session_id": turn.session_id,
                                "sentence_index": sentence.sentence_index,
                                "audio_format": "pcm16",
                                "sample_rate": 24000,
                                "channels": 1,
                                "reply_meta": sentence.reply_meta,
                            }
                        )
                        sentence.audio_started_sent = True
                    await self._send_uniapp_event(
                        {
                            "type": "reply_audio_done",
                            "message_id": turn.message_id,
                            "session_id": turn.session_id,
                            "sentence_index": sentence.sentence_index,
                            "reply_meta": sentence.reply_meta,
                        }
                    )
                    sentence.audio_sent = True
                    turn.next_audio_dispatch_index += 1
                    turn.audio_terminal_count += 1
                    if not turn.first_audio_sent:
                        self.logger.bind(tag=TAG).info(
                            f"first_audio_sent: idx={sentence.sentence_index}"
                        )
                        turn.first_audio_sent = True
                    continue

                if sentence.status == "audio_failed" and sentence.audio_chunks.empty():
                    await self._emit_reply_start_if_needed_async(turn)
                    await self._send_uniapp_event(
                        {
                            "type": "reply_audio_failed",
                            "message_id": turn.message_id,
                            "session_id": turn.session_id,
                            "sentence_index": sentence.sentence_index,
                            "reason": sentence.audio_failure_reason or "tts_failed",
                            "reply_meta": sentence.reply_meta,
                        }
                    )
                    sentence.audio_sent = True
                    turn.next_audio_dispatch_index += 1
                    continue

            if sentence.status == "audio_ready" and not sentence.audio_sent:
                await self._emit_reply_start_if_needed_async(turn)
                sentence.audio_sent_timestamp_ms = int(time.time() * 1000)
                sentence.audio_start_ms = turn.audio_timeline_ms
                if sentence.audio_duration_ms is not None:
                    sentence.audio_end_ms = sentence.audio_start_ms + sentence.audio_duration_ms
                else:
                    sentence.audio_end_ms = None
                payload = {
                    "type": "reply_audio_chunk",
                    "message_id": turn.message_id,
                    "session_id": turn.session_id,
                    "sentence_index": sentence.sentence_index,
                    "audio_url": sentence.audio_url,
                    "audio_base64": sentence.audio_base64,
                    "audio_format": sentence.audio_format,
                    "audio_start_ms": sentence.audio_start_ms,
                    "audio_end_ms": sentence.audio_end_ms,
                    "audio_duration_ms": sentence.audio_duration_ms,
                    "audio_ready_timestamp_ms": sentence.audio_ready_timestamp_ms,
                    "server_timestamp_ms": sentence.audio_sent_timestamp_ms,
                    "reply_meta": sentence.reply_meta,
                }
                await self._send_uniapp_event(payload)
                sentence.audio_sent = True
                turn.next_audio_dispatch_index += 1
                turn.audio_terminal_count += 1
                if sentence.audio_duration_ms is not None:
                    turn.audio_timeline_ms = sentence.audio_end_ms or turn.audio_timeline_ms
                if sentence.audio_path:
                    self._pending_sentence_audio_files.discard(sentence.audio_path)
                    turn.sent_audio_files.append(sentence.audio_path)
                if not turn.first_audio_sent:
                    self.logger.bind(tag=TAG).info(
                        f"first_audio_sent: idx={sentence.sentence_index}"
                    )
                    turn.first_audio_sent = True
                continue

            if sentence.status in {"audio_failed", "skipped"}:
                if not sentence.audio_sent:
                    await self._emit_reply_start_if_needed_async(turn)
                    await self._send_uniapp_event(
                        {
                            "type": "reply_audio_failed",
                            "message_id": turn.message_id,
                            "session_id": turn.session_id,
                            "sentence_index": sentence.sentence_index,
                            "reason": sentence.audio_failure_reason or sentence.status,
                            "reply_meta": sentence.reply_meta,
                        }
                    )
                    sentence.audio_sent = True
                turn.next_audio_dispatch_index += 1
                continue

            await asyncio.sleep(0.05)

        turn.audio_dispatch_done = True

    def _schedule_sentence_audio(self, turn: ReplyTurn, sentence: SentenceUnit) -> None:
        if self.tts is None or not getattr(self, "enable_audio_response", False):
            sentence.audio_skipped = True
            sentence.status = "skipped"
            turn.audio_terminal_count += 1
            return
        sentence.status = "queued"
        self.executor.submit(self._generate_sentence_audio, turn, sentence)

    def _finalize_reply_turn(self, turn: ReplyTurn) -> None:
        if turn.text_final_sent:
            return
        full_text = (turn.full_display_text or "").strip()
        payload = {
            "type": "reply_text_final",
            "message_id": turn.message_id,
            "session_id": turn.session_id,
            "text": full_text,
            "sentence_count": len(turn.sentences),
            "reply_meta": self._build_sentence_reply_meta(),
        }
        self._emit_reply_start_if_needed(turn)
        self._send_uniapp_event_sync(payload)
        turn.text_final_sent = True
        from core.handle.reportHandle import enqueue_tts_report

        enqueue_tts_report(self, full_text, [])
        compatibility_reply = {
            "type": "reply",
            "text": full_text,
            "session_id": self.session_id,
            "state": "complete",
            "message_id": turn.message_id,
        }
        compatibility_reply.update(self._build_sentence_reply_meta())
        self._send_uniapp_event_sync(compatibility_reply)

    def _emit_reply_end(self, turn: ReplyTurn, status: str) -> None:
        if turn.reply_end_sent:
            return
        if turn.sent_audio_files and self.chat_history_conf > 0 and self.report_tts_enable:
            try:
                audio_bytes = bytearray()
                for audio_path in turn.sent_audio_files:
                    if os.path.exists(audio_path):
                        with open(audio_path, "rb") as audio_file:
                            audio_bytes.extend(audio_file.read())
                if audio_bytes:
                    report_time_ms = int(time.time() * 1000)
                    agent_id = getattr(self, "current_agent_id", None)
                    self.executor.submit(
                        report_with_audio_bytes,
                        self,
                        2,
                        (turn.full_display_text or "").strip(),
                        bytes(audio_bytes),
                        report_time_ms,
                        agent_id,
                    )
            except Exception as e:
                self.logger.bind(tag=TAG).warning(
                    f"uniapp sentence audio report failed: {e}"
                )
        payload = {
            "type": "reply_end",
            "message_id": turn.message_id,
            "session_id": turn.session_id,
            "status": status,
            "sentence_count": len(turn.sentences),
            "audio_chunk_count": len([s for s in turn.sentences.values() if s.audio_sent]),
            "reply_meta": self._build_sentence_reply_meta(),
        }
        self._send_uniapp_event_sync(payload)
        turn.reply_end_sent = True
        with self.reply_turn_lock:
            self._active_reply_message_ids.discard(turn.message_id)
            if self.current_reply_turn and self.current_reply_turn.message_id == turn.message_id:
                self.current_reply_turn = None

    def _wait_reply_turn_completion(self, turn: ReplyTurn) -> None:
        while True:
            if turn.aborted:
                break
            sentence_count = len(turn.sentences)
            if turn.llm_completed and turn.audio_terminal_count >= sentence_count:
                break
            time.sleep(0.05)
        while not turn.audio_dispatch_done:
            time.sleep(0.05)
        if turn.text_final_sent:
            return
        if not turn.sentences and not turn.full_display_text.strip():
            self._emit_reply_end(turn, "aborted" if turn.aborted else "completed")
            return
        if turn.aborted and not turn.full_display_text.strip():
            self._emit_reply_end(turn, "aborted")
            return
        status = "completed"
        if turn.aborted:
            status = "aborted"
        elif any(sentence.audio_failed for sentence in turn.sentences.values()):
            status = "partial"
        self._finalize_reply_turn(turn)
        self._emit_reply_end(turn, status)

    def _build_sentences_from_buffer(
        self,
        buffer: str,
        *,
        force_flush: bool,
        backpressure_active: bool,
    ) -> tuple[list[str], str]:
        soft_limit, hard_limit = self._get_sentence_limits()
        local_force_flush = force_flush
        if backpressure_active and len(buffer) < hard_limit * 2 and not force_flush:
            return [], buffer
        if backpressure_active and len(buffer) >= hard_limit * 2:
            local_force_flush = True
        return split_sentence_buffer(
            buffer,
            soft_limit=soft_limit,
            hard_limit=hard_limit,
            min_sentence_chars=self._sentence_pipeline_config["min_sentence_chars"],
            force_flush=local_force_flush,
        )

    def _pending_sentence_count(self, turn: ReplyTurn) -> int:
        return len(
            [
                item
                for item in turn.sentences.values()
                if (
                    item.status not in {"audio_ready", "audio_failed", "skipped"}
                    or (item.status == "audio_ready" and not item.audio_sent)
                )
            ]
        )

    def _dispatch_uniapp_response(self, text: str) -> None:
        if not getattr(self, "is_uniapp_client", False):
            return
        turn = self._create_reply_turn()
        asyncio.run_coroutine_threadsafe(
            self._dispatch_sentence_audio_in_order(turn),
            self.loop,
        )
        buffer = text or ""
        sentences, remaining = self._build_sentences_from_buffer(
            buffer,
            force_flush=True,
            backpressure_active=False,
        )
        if remaining.strip():
            sentences.append(remaining.strip())
        for idx, item in enumerate(sentences):
            sentence = self._register_sentence_unit(
                turn,
                item,
                is_final=(idx == len(sentences) - 1),
            )
            self._send_sentence_text_chunk(turn, sentence)
            self._schedule_sentence_audio(turn, sentence)
        turn.llm_completed = True
        self._wait_reply_turn_completion(turn)

    async def _send_uniapp_reply(self, text: str, message_id: str | None = None):
        del message_id
        await asyncio.get_event_loop().run_in_executor(
            self.executor,
            lambda: self._dispatch_uniapp_response(text),
        )

    def _report_worker(self):
        """聊天记录上报工作线程"""
        while True:
            if self.stop_event.is_set() and self.report_queue.empty():
                break
            try:
                # 从队列获取数据，设置超时以便定期检查停止事件
                item = self.report_queue.get(timeout=1)
                if item is None:  # 检测毒丸对象
                    self.report_queue.task_done()
                    break
                try:
                    # 检查线程池状态
                    if self.executor is None:
                        continue
                    # 提交任务到线程池
                    self.executor.submit(self._process_report, *item)
                except Exception as e:
                    self.logger.bind(tag=TAG).error(f"聊天记录上报线程异常: {e}")
            except queue.Empty:
                continue
            except Exception as e:
                self.logger.bind(tag=TAG).error(f"聊天记录上报工作线程异常: {e}")

        self.logger.bind(tag=TAG).info("聊天记录上报线程已退出")

    def _process_report(self, type, text, audio_data, report_time, agent_id=None):
        """处理上报任务"""
        try:
            # 执行上报（传入二进制数据和 agent_id）
            report(self, type, text, audio_data, report_time, agent_id)
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"上报处理异常: {e}")
        finally:
            # 标记任务完成
            self.report_queue.task_done()

    def clearSpeakStatus(self):
        self.client_is_speaking = False
        self.logger.bind(tag=TAG).debug(f"清除服务端讲话状态")

    def abort_current_reply_turn(self) -> None:
        with self.reply_turn_lock:
            turn = self.current_reply_turn
            if turn:
                turn.aborted = True
                self._active_reply_message_ids.discard(turn.message_id)
        self._cleanup_pending_sentence_audio_files()

    def _cleanup_pending_sentence_audio_files(self) -> None:
        pending_files = list(self._pending_sentence_audio_files)
        self._pending_sentence_audio_files.clear()
        for audio_path in pending_files:
            try:
                if audio_path and os.path.exists(audio_path):
                    os.remove(audio_path)
            except Exception as e:
                self.logger.bind(tag=TAG).debug(
                    f"cleanup pending sentence audio failed: path={audio_path} error={e}"
                )

    async def close(self, ws=None):
        """资源清理方法"""
        try:
            self.abort_current_reply_turn()
            # 清理音频缓冲区
            if hasattr(self, "audio_buffer"):
                self.audio_buffer.clear()

            # 取消超时任务
            if self.timeout_task and not self.timeout_task.done():
                self.timeout_task.cancel()
                try:
                    await self.timeout_task
                except asyncio.CancelledError:
                    pass
                self.timeout_task = None

            # 清理工具处理器资源
            if hasattr(self, "func_handler") and self.func_handler:
                try:
                    await self.func_handler.cleanup()
                except Exception as cleanup_error:
                    self.logger.bind(tag=TAG).error(
                        f"清理工具处理器时出错: {cleanup_error}"
                    )

            # 触发停止事件
            if self.stop_event:
                self.stop_event.set()

            # 清空任务队列
            self.clear_queues()

            # 在关闭执行器前，尽量等待上报队列排空，避免断连丢历史
            self._wait_report_queue_flush(timeout_seconds=3.0)

            # 关闭WebSocket连接
            try:
                if ws:
                    # 安全地检查WebSocket状态并关闭
                    try:
                        if hasattr(ws, "closed") and not ws.closed:
                            await ws.close()
                        elif hasattr(ws, "state") and ws.state.name != "CLOSED":
                            await ws.close()
                        else:
                            # 如果没有closed属性，直接尝试关闭
                            await ws.close()
                    except Exception:
                        # 如果关闭失败，忽略错误
                        pass
                elif self.websocket:
                    try:
                        if (
                            hasattr(self.websocket, "closed")
                            and not self.websocket.closed
                        ):
                            await self.websocket.close()
                        elif (
                            hasattr(self.websocket, "state")
                            and self.websocket.state.name != "CLOSED"
                        ):
                            await self.websocket.close()
                        else:
                            # 如果没有closed属性，直接尝试关闭
                            await self.websocket.close()
                    except Exception:
                        # 如果关闭失败，忽略错误
                        pass
            except Exception as ws_error:
                self.logger.bind(tag=TAG).error(f"关闭WebSocket连接时出错: {ws_error}")

            if self.tts:
                await self.tts.close()

            # 最后关闭线程池（避免阻塞）
            if self.executor:
                try:
                    self.executor.shutdown(wait=False)
                except Exception as executor_error:
                    self.logger.bind(tag=TAG).error(
                        f"关闭线程池时出错: {executor_error}"
                    )
                self.executor = None

            self.logger.bind(tag=TAG).info("连接资源已释放")
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"关闭连接时出错: {e}")
        finally:
            # 确保停止事件被设置
            if self.stop_event:
                self.stop_event.set()

    def clear_queues(self):
        """清空所有任务队列"""
        self.abort_current_reply_turn()
        if self.tts:
            self.logger.bind(tag=TAG).debug(
                f"开始清理: TTS队列大小={self.tts.tts_text_queue.qsize()}, 音频队列大小={self.tts.tts_audio_queue.qsize()}"
            )

            # 使用非阻塞方式清空队列
            for q in [
                self.tts.tts_text_queue,
                self.tts.tts_audio_queue,
            ]:
                if not q:
                    continue
                while True:
                    try:
                        q.get_nowait()
                    except queue.Empty:
                        break

            self.logger.bind(tag=TAG).debug(
                f"清理结束: TTS队列大小={self.tts.tts_text_queue.qsize()}, 音频队列大小={self.tts.tts_audio_queue.qsize()}"
            )

    def _wait_report_queue_flush(self, timeout_seconds=3.0):
        """等待上报队列排空，避免连接关闭导致未上报消息丢失。"""
        try:
            deadline = time.time() + max(0.1, float(timeout_seconds))
            while time.time() < deadline:
                if self.report_queue.unfinished_tasks <= 0:
                    return
                time.sleep(0.05)
            self.logger.bind(tag=TAG).warning(
                f"上报队列排空超时，剩余任务={self.report_queue.unfinished_tasks}"
            )
        except Exception as e:
            self.logger.bind(tag=TAG).warning(f"等待上报队列排空失败: {e}")

    def reset_vad_states(self):
        self.client_audio_buffer = bytearray()
        self.client_have_voice = False
        self.client_voice_stop = False
        self.logger.bind(tag=TAG).debug("VAD states reset.")

    def chat_and_close(self, text):
        """Chat with the user and then close the connection"""
        try:
            # Use the existing chat method
            self.chat(text)

            # After chat is complete, close the connection
            self.close_after_chat = True
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"Chat and close error: {str(e)}")

    async def _check_timeout(self):
        """检查连接超时"""
        try:
            while not self.stop_event.is_set():
                # 检查是否超时（只有在时间戳已初始化的情况下）
                if self.last_activity_time > 0.0:
                    current_time = time.time() * 1000
                    if (
                        current_time - self.last_activity_time
                        > self.timeout_seconds * 1000
                    ):
                        if not self.stop_event.is_set():
                            self.logger.bind(tag=TAG).info("连接超时，准备关闭")
                            # 设置停止事件，防止重复处理
                            self.stop_event.set()
                            # 使用 try-except 包装关闭操作，确保不会因为异常而阻塞
                            try:
                                await self.close(self.websocket)
                            except Exception as close_error:
                                self.logger.bind(tag=TAG).error(
                                    f"超时关闭连接时出错: {close_error}"
                                )
                        break
                # 每10秒检查一次，避免过于频繁
                await asyncio.sleep(10)
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"超时检查任务出错: {e}")
        finally:
            self.logger.bind(tag=TAG).info("超时检查任务已退出")

