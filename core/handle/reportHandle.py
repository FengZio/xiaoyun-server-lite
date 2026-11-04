"""
TTS上报功能已集成到ConnectionHandler类中。

上报功能包括：
1. 每个连接对象拥有自己的上报队列和处理线程
2. 上报线程的生命周期与连接对象绑定
3. 使用ConnectionHandler.enqueue_tts_report方法进行上报

具体实现请参考core/connection.py中的相关代码。
"""

import time
import io
import wave

import opuslib_next

from config.manage_api_client import report as manage_report

TAG = __name__


def _resolve_report_device_id(conn, agent_id=None):
    if getattr(conn, "device_id", None):
        return conn.device_id
    headers = getattr(conn, "headers", {}) or {}
    fallback = headers.get("device-id") or headers.get("client-id") or agent_id or conn.session_id
    if fallback:
        conn.device_id = f"uniapp-{fallback}"
        conn.logger.bind(tag=TAG).warning(
            f"report macAddress is empty, fallback to {conn.device_id}"
        )
    return conn.device_id


def report(conn, type, text, opus_data, report_time, agent_id=None):
    """执行聊天记录上报操作

    Args:
        conn: 连接对象
        type: 上报类型，1为用户，2为智能体
        text: 合成文本
        opus_data: opus音频数据
        report_time: 上报时间
        agent_id: 智能体ID（可选，不传则由后端使用设备默认智能体）
    """
    try:
        if opus_data:
            audio_data = audio_chunks_to_wav(conn, opus_data)
        else:
            audio_data = None
        # 执行上报
        report_device_id = _resolve_report_device_id(conn, agent_id)
        result = manage_report(
            mac_address=report_device_id,
            session_id=conn.session_id,
            chat_type=type,
            content=text,
            audio=audio_data,
            report_time=report_time,
            agent_id=agent_id,
        )
        conn.logger.bind(tag=TAG).info(
            f"chat-history report result={result} mac={report_device_id} "
            f"session={conn.session_id} chat_type={type} agent={agent_id}"
        )
    except Exception as e:
        conn.logger.bind(tag=TAG).error(f"聊天记录上报失败: {e}")



def report_with_audio_bytes(conn, type, text, audio_bytes, report_time, agent_id=None):
    """直接上报原始音频字节，适用于非Opus链路（如 UniApp MiMo TTS）。"""
    try:
        report_device_id = _resolve_report_device_id(conn, agent_id)
        result = manage_report(
            mac_address=report_device_id,
            session_id=conn.session_id,
            chat_type=type,
            content=text,
            audio=audio_bytes,
            report_time=report_time,
            agent_id=agent_id,
        )
        conn.logger.bind(tag=TAG).info(
            f"chat-history binary report result={result} mac={report_device_id} "
            f"session={conn.session_id} chat_type={type} agent={agent_id} "
            f"audio_bytes={len(audio_bytes) if audio_bytes else 0}"
        )
    except Exception as e:
        conn.logger.bind(tag=TAG).error(f"聊天记录二进制音频上报失败: {e}")
def audio_chunks_to_wav(conn, audio_chunks):
    """将当前连接收到的音频片段统一转换成WAV字节流。"""
    from core.providers.asr.base import ASRProviderBase

    audio_format = getattr(conn, "audio_format", "opus")
    pcm_data = ASRProviderBase.normalize_audio_chunks(audio_chunks, audio_format)
    if not pcm_data:
        raise ValueError("没有有效的PCM数据")

    pcm_data_bytes = b"".join(pcm_data)
    wav_buffer = io.BytesIO()
    with wave.open(wav_buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(pcm_data_bytes)
    return wav_buffer.getvalue()


def enqueue_tts_report(conn, text, opus_data):
    if conn.need_bind or not conn.report_tts_enable:
        return
    if conn.chat_history_conf == 0:
        return
    """将TTS数据加入上报队列

    Args:
        conn: 连接对象
        text: 合成文本
        opus_data: opus音频数据
    """
    try:
        # 获取当前智能体ID
        agent_id = getattr(conn, 'current_agent_id', None)
        # 使用连接对象的队列，传入文本和二进制数据而非文件路径
        report_time_ms = int(time.time() * 1000)
        if conn.chat_history_conf == 2:
            conn.report_queue.put((2, text, opus_data, report_time_ms, agent_id))
            conn.logger.bind(tag=TAG).debug(
                f"TTS数据已加入上报队列: {conn.device_id}, 智能体: {agent_id}, 音频大小: {len(opus_data)} "
            )
        else:
            conn.report_queue.put((2, text, None, report_time_ms, agent_id))
            conn.logger.bind(tag=TAG).debug(
                f"TTS数据已加入上报队列: {conn.device_id}, 智能体: {agent_id}, 不上报音频"
            )
    except Exception as e:
        conn.logger.bind(tag=TAG).error(f"加入TTS上报队列失败: {text}, {e}")


def enqueue_asr_report(conn, text, opus_data):
    if conn.need_bind or not conn.report_asr_enable:
        return
    if conn.chat_history_conf == 0:
        return
    """将ASR数据加入上报队列

    Args:
        conn: 连接对象
        text: 合成文本
        opus_data: opus音频数据
    """
    try:
        # 获取当前智能体ID
        agent_id = getattr(conn, 'current_agent_id', None)
        # 使用连接对象的队列，传入文本和二进制数据而非文件路径
        report_time_ms = int(time.time() * 1000)
        if conn.chat_history_conf == 2:
            conn.report_queue.put((1, text, opus_data, report_time_ms, agent_id))
            conn.logger.bind(tag=TAG).debug(
                f"ASR数据已加入上报队列: {conn.device_id}, 智能体: {agent_id}, 音频大小: {len(opus_data)} "
            )
        else:
            conn.report_queue.put((1, text, None, report_time_ms, agent_id))
            conn.logger.bind(tag=TAG).debug(
                f"ASR数据已加入上报队列: {conn.device_id}, 智能体: {agent_id}, 不上报音频"
            )
    except Exception as e:
        conn.logger.bind(tag=TAG).debug(f"加入ASR上报队列失败: {text}, {e}")

