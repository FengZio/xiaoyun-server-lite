import time
import json
import uuid
import threading
from typing import Dict, Any
from core.handle.textMessageHandler import TextMessageHandler
from core.handle.textMessageType import TextMessageType
from core.utils.client_context import normalize_entry_context

TAG = __name__


class ChatTextMessageHandler(TextMessageHandler):
    """UniApp 聊天消息处理器"""

    @property
    def message_type(self) -> TextMessageType:
        return TextMessageType.CHAT

    async def handle(self, conn, msg_json: Dict[str, Any]) -> None:
        """
        处理 UniApp 聊天消息
        
        接收格式：
        {
            "type": "chat",
            "content": "用户消息",
            "enable_audio": true,  # 可选，是否生成音频
            "agent_id": "xxx",     # 可选，指定智能体ID
            "session_id": "xxx"    # 可选，续接历史会话ID
        }
        """
        content = msg_json.get("content", "")
        enable_audio = msg_json.get("enable_audio", True)  # 默认启用音频
        agent_id = msg_json.get("agent_id", None)  # 获取智能体ID
        entry_context = normalize_entry_context(
            msg_json.get("entry_context", {}),
            existing=getattr(conn, "entry_context", {}),
        )
        self._adopt_client_session_id(conn, msg_json)
        
        if not content:
            conn.logger.bind(tag=TAG).warning("收到空的聊天消息")
            await self._send_error_reply(conn, "消息内容不能为空")
            return
        
        # 如果指定了 agent_id，记录日志
        if agent_id:
            conn.logger.bind(tag=TAG).info(
                f"UniApp 聊天消息: {content}, 启用音频: {enable_audio}, 智能体ID: {agent_id}"
            )
        else:
            conn.logger.bind(tag=TAG).info(
                f"UniApp 聊天消息: {content}, 启用音频: {enable_audio}"
            )
        
        # 更新活动时间
        conn.last_activity_time = time.time() * 1000
        
        # 处理智能体切换（如果指定了 agent_id）
        if agent_id:
            switch_success = await self._switch_agent_if_needed(conn, agent_id)
            if not switch_success:
                # 切换失败，直接返回（错误消息已在 _switch_agent_if_needed 中发送）
                return
        
        # 检查是否是唤醒词
        from core.utils.util import remove_punctuation_and_length
        from core.handle.helloHandle import checkWakeupWords
        
        filtered_len, filtered_text = remove_punctuation_and_length(content)
        is_wakeup = filtered_text in conn.config.get("wakeup_words", [])
        
        if is_wakeup and await checkWakeupWords(conn, content):
            # 唤醒词已处理，直接返回
            conn.logger.bind(tag=TAG).info("唤醒词已处理")
            return
        
        # 标记为 UniApp 客户端
        conn.is_uniapp_client = True
        conn.enable_audio_response = enable_audio
        conn.uniapp_audio_files = []  # 存储生成的音频文件路径
        conn.last_user_message = content
        conn.entry_context = entry_context
        conn.logger.bind(tag=TAG).info("标记为 UniApp 客户端")

        # manager-api 上报要求 macAddress 非空；UniApp 常见无 device-id 头，做兜底映射
        self._ensure_report_device_id(conn, msg_json, agent_id)
        
        # 上报用户消息（ASR 类型，chat_type=1）
        from core.handle.reportHandle import enqueue_asr_report
        enqueue_asr_report(conn, content, [])  # UniApp 文本消息没有音频
        
        # 添加到对话历史
        
        # 开始 LLM 对话
        await self._start_chat(conn, content)

    def _ensure_report_device_id(self, conn, msg_json: Dict[str, Any], agent_id: str) -> None:
        if getattr(conn, "device_id", None):
            return
        headers = getattr(conn, "headers", {}) or {}
        fallback = (
            headers.get("client-id")
            or msg_json.get("client_id")
            or msg_json.get("user_id")
            or msg_json.get("uid")
            or agent_id
        )
        if fallback:
            conn.device_id = f"uniapp-{fallback}"
            conn.logger.bind(tag=TAG).warning(
                f"device_id 为空，使用兜底上报ID: {conn.device_id}"
            )

    def _adopt_client_session_id(self, conn, msg_json: Dict[str, Any]) -> None:
        """允许客户端在连接初期续接已有会话。"""
        try:
            incoming = msg_json.get("session_id") or msg_json.get("sessionId")
            if incoming is None:
                return
            incoming = str(incoming).strip()
            if not incoming:
                return
            if incoming == conn.session_id:
                return
            # 仅允许在本连接尚未进入真实对话时覆盖 session_id
            history_len = len(getattr(conn.dialogue, "dialogue", []))
            if history_len > 1:
                conn.logger.bind(tag=TAG).warning(
                    f"忽略 session_id 续接请求: 对话已开始, current={conn.session_id}, requested={incoming}"
                )
                return
            conn.logger.bind(tag=TAG).info(
                f"采用客户端 session_id: {conn.session_id} -> {incoming}"
            )
            conn.session_id = incoming
            conn.session_id_adopted = True
            if isinstance(getattr(conn, "welcome_msg", None), dict):
                conn.welcome_msg["session_id"] = conn.session_id
        except Exception as e:
            conn.logger.bind(tag=TAG).warning(f"处理 session_id 续接失败: {e}")

    
    async def _start_chat(self, conn, text: str):
        """开始聊天并生成回复"""
        import asyncio
        
        # 检查LLM是否已初始化
        if conn.llm is None:
            conn.logger.bind(tag=TAG).error("LLM未初始化，无法处理聊天消息")
            await self._send_error_reply(conn, "抱歉，AI服务尚未准备好，请稍后再试")
            return
        
        def chat_task():
            try:
                return conn.chat(text, depth=0)
            except Exception as e:
                conn.logger.bind(tag=TAG).error(f"LLM 对话失败: {e}")
                import traceback
                conn.logger.bind(tag=TAG).error(f"错误堆栈: {traceback.format_exc()}")
                return None
        
        try:
            # 在线程池中执行 chat（避免阻塞事件循环）
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(conn.executor, chat_task)
        except Exception as e:
            conn.logger.bind(tag=TAG).error(f"聊天处理失败: {e}")
            await self._send_error_reply(conn, "抱歉，处理您的消息时出错了")
    
    async def _switch_agent_if_needed(self, conn, agent_id: str) -> bool:
        """
        如果需要，切换到指定的智能体
        
        Args:
            conn: 连接对象
            agent_id: 要切换到的智能体ID
            
        Returns:
            bool: True 表示切换成功或无需切换，False 表示切换失败
        """
        try:
            # 检查是否已经是当前智能体
            current_agent_id = getattr(conn, 'current_agent_id', None)
            had_llm_before_switch = conn.llm is not None
            current_selected_module = conn.config.get("selected_module", {}) or {}
            missing_runtime_modules = [
                module_name
                for module_name in ("Intent", "VLLM")
                if not current_selected_module.get(module_name) or not conn.config.get(module_name)
            ]
            if current_agent_id == agent_id and not missing_runtime_modules:
                conn.logger.bind(tag=TAG).debug(f"已经在使用智能体 {agent_id}，无需切换")
                return True
            if current_agent_id == agent_id and missing_runtime_modules:
                conn.logger.bind(tag=TAG).warning(
                    f"检测到当前智能体运行态配置缺失，执行强制刷新: agent_id={agent_id}, missing={missing_runtime_modules}"
                )
            
            conn.logger.bind(tag=TAG).info(f"开始切换智能体: {current_agent_id} -> {agent_id}")
            
            # 从 Manager API 获取智能体配置
            from config.manage_api_client import get_agent_config
            
            agent_config = get_agent_config(conn.config, agent_id)
            
            if not agent_config:
                conn.logger.bind(tag=TAG).warning(f"无法获取智能体配置: {agent_id}")
                # 兼容旧前端缓存失效agent_id的场景：有可用LLM时回退到当前智能体继续对话
                if had_llm_before_switch:
                    conn.logger.bind(tag=TAG).warning(
                        f"回退到当前智能体继续对话: {current_agent_id}"
                    )
                    return True
                await self._send_error_reply(conn, f"智能体 {agent_id} 不存在或无法访问")
                return False
            
            # 有 agent_config 说明服务端可用，取消绑定态以允许上报
            conn.need_bind = False
            conn.bind_code = None
            
            # 更新 LLM 配置
            if agent_config.get("LLM"):
                conn.config["LLM"] = agent_config["LLM"]
                conn.config["selected_module"]["LLM"] = agent_config["selected_module"]["LLM"]
                
                # 重新初始化 LLM
                from core.utils import llm as llm_utils
                selected_llm = agent_config["selected_module"]["LLM"]
                if selected_llm in agent_config["LLM"]:
                    llm_config = agent_config["LLM"][selected_llm]
                    llm_type = llm_config.get("type", selected_llm)
                    conn.llm = llm_utils.create_instance(llm_type, llm_config)
                    conn.logger.bind(tag=TAG).info(f"已切换到智能体 {agent_id} 的 LLM: {selected_llm}")
                else:
                    conn.logger.bind(tag=TAG).error(
                        f"智能体 {agent_id} 的 selected_module.LLM={selected_llm} 不在 LLM 配置中"
                    )
                    await self._send_error_reply(conn, "智能体LLM配置不完整，无法切换")
                    return False
            elif not had_llm_before_switch:
                conn.logger.bind(tag=TAG).error(
                    f"智能体 {agent_id} 未提供 LLM 配置，且当前连接无可用 LLM"
                )
                await self._send_error_reply(conn, "智能体未配置可用LLM，无法处理聊天")
                return False

            if conn.llm is None:
                conn.logger.bind(tag=TAG).error(f"智能体 {agent_id} 切换后 LLM 仍未初始化")
                await self._send_error_reply(conn, "智能体LLM初始化失败，请检查配置")
                return False

            # 更新 VLLM 配置（视觉模型）
            if agent_config.get("VLLM"):
                conn.config["VLLM"] = agent_config["VLLM"]
                if agent_config.get("selected_module", {}).get("VLLM"):
                    conn.config["selected_module"]["VLLM"] = agent_config["selected_module"]["VLLM"]
                conn.logger.bind(tag=TAG).info(
                    f"已同步智能体 {agent_id} 的 VLLM: "
                    f"selected={conn.config.get('selected_module', {}).get('VLLM')}, "
                    f"keys={list((conn.config.get('VLLM') or {}).keys())}"
                )

            # 更新 Intent 配置
            if agent_config.get("Intent"):
                conn.config["Intent"] = agent_config["Intent"]
                if agent_config.get("selected_module", {}).get("Intent"):
                    conn.config["selected_module"]["Intent"] = agent_config["selected_module"]["Intent"]
                conn.logger.bind(tag=TAG).info(
                    f"已同步智能体 {agent_id} 的 Intent: "
                    f"selected={conn.config.get('selected_module', {}).get('Intent')}, "
                    f"keys={list((conn.config.get('Intent') or {}).keys())}"
                )

            # 更新 TTS 配置，并保留本地敏感字段（api_key/api_url 等）
            if agent_config.get("TTS"):
                local_tts_config = dict(conn.config.get("TTS", {}))
                conn.config["TTS"] = agent_config["TTS"]
                if agent_config.get("selected_module", {}).get("TTS"):
                    conn.config["selected_module"]["TTS"] = agent_config["selected_module"]["TTS"]

                selected_tts = conn.config["selected_module"].get("TTS")
                if selected_tts and selected_tts in conn.config["TTS"]:
                    agent_tts_conf = conn.config["TTS"][selected_tts]
                    local_tts_conf = local_tts_config.get(selected_tts, {})
                    preserve_fields = [
                        "api_key", "access_token", "appid", "app_id",
                        "authorization", "token", "secret", "api_url",
                        "base_url", "url", "host",
                    ]
                    for field in preserve_fields:
                        local_value = local_tts_conf.get(field)
                        if (
                            isinstance(local_value, str)
                            and local_value.strip()
                            and "你的" not in local_value
                            and (not agent_tts_conf.get(field) or "你的" in str(agent_tts_conf.get(field)))
                        ):
                            agent_tts_conf[field] = local_value

                try:
                    if conn.tts is not None:
                        await conn.tts.close()
                except Exception as e:
                    conn.logger.bind(tag=TAG).warning(f"关闭旧TTS实例失败: {e}")

                try:
                    conn.tts = conn._initialize_tts()
                    if conn.tts is not None:
                        await conn.tts.open_audio_channels(conn)
                        resolved_tts = conn.config.get("selected_module", {}).get("TTS")
                        resolved_tts_conf = conn.config.get("TTS", {}).get(resolved_tts, {})
                        conn.logger.bind(tag=TAG).info(
                            f"已切换到智能体 {agent_id} 的 TTS: {resolved_tts}, "
                            f"type={resolved_tts_conf.get('type')}, "
                            f"has_api_key={bool(resolved_tts_conf.get('api_key'))}, "
                            f"api_url={resolved_tts_conf.get('api_url')}"
                        )
                except Exception as e:
                    conn.logger.bind(tag=TAG).error(f"重建TTS实例失败: {e}")
                    await self._send_error_reply(conn, "智能体TTS初始化失败，请检查配置")
                    return False

            # 到这里说明切换成功，再更新当前智能体ID
            conn.current_agent_id = agent_id
            
            # 更新 Prompt
            if agent_config.get("prompt"):
                conn.config["prompt"] = agent_config["prompt"]
                conn.prompt = agent_config["prompt"]
                # 更新系统 prompt 到对话上下文
                if hasattr(conn, 'dialogue'):
                    conn.dialogue.update_system_message(conn.prompt)
                conn.logger.bind(tag=TAG).info(f"已更新智能体 {agent_id} 的 Prompt")

            # 同步聊天记录上报配置（agent-config）
            chat_history_conf = agent_config.get("chat_history_conf")
            if chat_history_conf is None:
                # 兜底：未返回时默认开启文本+音频上报
                chat_history_conf = 2
                conn.logger.bind(tag=TAG).warning(
                    "agent_config 未包含 chat_history_conf，默认开启文本+音频上报"
                )
            if chat_history_conf is not None:
                try:
                    conf_val = int(chat_history_conf)
                    if conf_val <= 0:
                        # 强制默认开启文本+音频上报
                        conf_val = 2
                        conn.logger.bind(tag=TAG).warning(
                            "chat_history_conf=0，已强制改为2(文本+音频上报)"
                        )
                    conn.chat_history_conf = conf_val
                except Exception:
                    conn.chat_history_conf = 2
                # 即便未从 agent-models 拉配置，也允许上报
                conn.report_asr_enable = True
                conn.report_tts_enable = True
                if conn.chat_history_conf > 0:
                    if conn.report_thread is None or not conn.report_thread.is_alive():
                        conn.report_thread = threading.Thread(
                            target=conn._report_worker, daemon=True
                        )
                        conn.report_thread.start()
                        conn.logger.bind(tag=TAG).info(
                            f"聊天记录上报线程已启动(chat_history_conf={conn.chat_history_conf})"
                        )
            
            # 清空对话历史（切换智能体时重新开始）
            if hasattr(conn, 'dialogue'):
                # 直接清空对话列表
                conn.dialogue.dialogue.clear()
                # 重新添加系统消息
                conn.dialogue.update_system_message(conn.prompt)
                conn.logger.bind(tag=TAG).info("已清空对话历史")
            
            # 重新初始化记忆（如果需要）
            if agent_config.get("Memory"):
                conn.config["Memory"] = agent_config["Memory"]
                conn.config["selected_module"]["Memory"] = agent_config["selected_module"]["Memory"]
                # 这里可以选择重新初始化记忆模块
                # 目前保持现有记忆模块不变
            
            conn.logger.bind(tag=TAG).success(f"✅ 成功切换到智能体: {agent_id}")
            return True
            
        except Exception as e:
            conn.logger.bind(tag=TAG).error(f"切换智能体失败: {e}")
            import traceback
            conn.logger.bind(tag=TAG).error(f"错误堆栈: {traceback.format_exc()}")
            await self._send_error_reply(conn, f"切换智能体失败: {str(e)}")
            return False


    
    async def _send_error_reply(self, conn, error_message: str):
        """发送错误回复"""
        reply = {
            "type": "reply",
            "text": error_message,
            "session_id": conn.session_id,
            "state": "error",
            "error": True
        }
        await conn.websocket.send(json.dumps(reply))
