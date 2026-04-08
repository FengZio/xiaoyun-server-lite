import os
import time
import queue
import aiohttp
import asyncio
import requests
import traceback
from urllib.parse import urlencode
from config.logger import setup_logging
from core.utils.tts import MarkdownCleaner
from core.providers.tts.base import TTSProviderBase
from core.utils import opus_encoder_utils, textUtils
from core.providers.tts.dto.dto import SentenceType, ContentType, InterfaceType

TAG = __name__
logger = setup_logging()


class TTSProvider(TTSProviderBase):
    def __init__(self, config, delete_audio_file):
        super().__init__(config, delete_audio_file)
        self.interface_type = InterfaceType.SINGLE_STREAM
        
        # 从配置中获取云服务器相关参数
        self.base_url = config.get("base_url", "")  # 基础URL，如: https://[你的实例ID]-9880.container.x-gpu.com
        self.speaker = config.get("speaker", "666.pt")  # 音色文件
        self.emotion = config.get("emotion", "高兴")  # 情感
        
        # 私有音色支持
        if config.get("private_voice"):
            self.speaker = config.get("private_voice")
        
        self.audio_format = "wav"  # 假设返回wav格式
        self.before_stop_play_files = []

        # 创建Opus编码器，假设采样率为24000（可根据实际情况调整）
        self.opus_encoder = opus_encoder_utils.OpusEncoderUtils(
            sample_rate=24000, channels=1, frame_size_ms=60
        )

        # 音频缓冲区
        self.audio_buffer = bytearray()

    def tts_text_priority_thread(self):
        """流式文本处理线程"""
        while not self.conn.stop_event.is_set():
            try:
                message = self.tts_text_queue.get(timeout=1)
                if message.sentence_type == SentenceType.FIRST:
                    # 初始化参数
                    self.tts_stop_request = False
                    self.processed_chars = 0
                    self.tts_text_buff = []
                    self.before_stop_play_files.clear()
                elif ContentType.TEXT == message.content_type:
                    self.tts_text_buff.append(message.content_detail)
                    segment_text = self._get_segment_text()
                    if segment_text:
                        self.to_tts_single_stream(segment_text)

                elif ContentType.FILE == message.content_type:
                    logger.bind(tag=TAG).info(
                        f"添加音频文件到待播放列表: {message.content_file}"
                    )
                    if message.content_file and os.path.exists(message.content_file):
                        # 先处理文件音频数据
                        self._process_audio_file_stream(message.content_file, callback=lambda audio_data: self.handle_audio_file(audio_data, message.content_detail))

                if message.sentence_type == SentenceType.LAST:
                    # 处理剩余的文本
                    self._process_remaining_text_stream(True)

            except queue.Empty:
                continue
            except Exception as e:
                logger.bind(tag=TAG).error(
                    f"处理TTS文本失败: {str(e)}, 类型: {type(e).__name__}, 堆栈: {traceback.format_exc()}"
                )

    def _process_remaining_text_stream(self, is_last=False):
        """处理剩余的文本并生成语音"""
        full_text = "".join(self.tts_text_buff)
        remaining_text = full_text[self.processed_chars :]
        if remaining_text:
            segment_text = textUtils.get_string_no_punctuation_or_emoji(remaining_text)
            if segment_text:
                self.to_tts_single_stream(segment_text, is_last)
                self.processed_chars += len(full_text)
            else:
                self._process_before_stop_play_files()
        else:
            self._process_before_stop_play_files()

    def to_tts_single_stream(self, text, is_last=False):
        try:
            max_repeat_time = 5
            text = MarkdownCleaner.clean_markdown(text)
            try:
                asyncio.run(self.text_to_speak(text, is_last))
            except Exception as e:
                logger.bind(tag=TAG).warning(
                    f"语音生成失败{5 - max_repeat_time + 1}次: {text}，错误: {e}"
                )
                max_repeat_time -= 1

            if max_repeat_time > 0:
                logger.bind(tag=TAG).info(
                    f"语音生成成功: {text}，重试{5 - max_repeat_time}次"
                )
            else:
                logger.bind(tag=TAG).error(
                    f"语音生成失败: {text}，请检查网络或服务是否正常"
                )
        except Exception as e:
            logger.bind(tag=TAG).error(f"Failed to generate TTS file: {e}")
        finally:
            return None

    def _build_api_url(self, text):
        """构建API请求URL"""
        params = {
            'text': text,
            'speaker': self.speaker,
            'emo': self.emotion
        }
        return f"{self.base_url}/?{urlencode(params)}"

    async def text_to_speak(self, text, is_last):
        """流式处理TTS音频"""
        api_url = self._build_api_url(text)
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url, timeout=30) as resp:
                    if resp.status != 200:
                        logger.bind(tag=TAG).error(
                            f"TTS请求失败: {resp.status}, {await resp.text()}"
                        )
                        self.tts_audio_queue.put((SentenceType.LAST, [], None))
                        return

                    self.audio_buffer.clear()
                    self.tts_audio_queue.put((SentenceType.FIRST, [], text))

                    # 读取完整的音频数据
                    audio_data = await resp.read()
                    
                    if audio_data:
                        # 将音频数据转换为PCM格式（如果需要的话）
                        # 这里假设返回的是WAV格式，需要提取PCM数据
                        pcm_data = self._extract_pcm_from_wav(audio_data)
                        
                        # 计算每帧的字节数
                        frame_bytes = int(
                            self.opus_encoder.sample_rate
                            * self.opus_encoder.channels  # 1
                            * self.opus_encoder.frame_size_ms
                            / 1000
                            * 2
                        )  # 16-bit = 2 bytes

                        # 分帧处理PCM数据
                        for i in range(0, len(pcm_data), frame_bytes):
                            frame = pcm_data[i : i + frame_bytes]
                            if len(frame) < frame_bytes:
                                # 最后一帧可能不足，用0填充
                                frame = frame + b"\x00" * (frame_bytes - len(frame))

                            self.opus_encoder.encode_pcm_to_opus_stream(
                                frame,
                                end_of_stream=(i + frame_bytes >= len(pcm_data)),
                                callback=self.handle_opus
                            )

                    # 如果是最后一段，输出音频获取完毕
                    if is_last:
                        self._process_before_stop_play_files()

        except Exception as e:
            logger.bind(tag=TAG).error(f"TTS请求异常: {e}")
            self.tts_audio_queue.put((SentenceType.LAST, [], None))

    def _extract_pcm_from_wav(self, wav_data):
        """从WAV数据中提取PCM数据"""
        try:
            # 简单的WAV头部解析（44字节标准WAV头）
            if len(wav_data) > 44 and wav_data[:4] == b'RIFF':
                # 跳过WAV头部，返回PCM数据
                return wav_data[44:]
            else:
                # 如果不是WAV格式，直接返回原始数据
                logger.bind(tag=TAG).warning("音频数据不是标准WAV格式，直接使用原始数据")
                return wav_data
        except Exception as e:
            logger.bind(tag=TAG).error(f"提取PCM数据失败: {e}")
            return wav_data

    async def close(self):
        """资源清理"""
        await super().close()
        if hasattr(self, "opus_encoder"):
            self.opus_encoder.close()

    def to_tts(self, text: str) -> list:
        """非流式TTS处理，用于测试及保存音频文件的场景"""
        start_time = time.time()
        text = MarkdownCleaner.clean_markdown(text)
        
        api_url = self._build_api_url(text)

        try:
            response = requests.get(api_url, timeout=30)
            if response.status_code != 200:
                logger.bind(tag=TAG).error(
                    f"TTS请求失败: {response.status_code}, {response.text}"
                )
                return []

            logger.info(f"TTS请求成功: {text}, 耗时: {time.time() - start_time}秒")

            # 提取PCM数据
            pcm_data = self._extract_pcm_from_wav(response.content)

            # 使用opus编码器处理PCM数据
            opus_datas = []

            # 计算每帧的字节数
            frame_bytes = int(
                self.opus_encoder.sample_rate
                * self.opus_encoder.channels
                * self.opus_encoder.frame_size_ms
                / 1000
                * 2
            )

            # 分帧处理PCM数据
            for i in range(0, len(pcm_data), frame_bytes):
                frame = pcm_data[i : i + frame_bytes]
                if len(frame) < frame_bytes:
                    # 最后一帧可能不足，用0填充
                    frame = frame + b"\x00" * (frame_bytes - len(frame))

                self.opus_encoder.encode_pcm_to_opus_stream(
                    frame,
                    end_of_stream=(i + frame_bytes >= len(pcm_data)),
                    callback=lambda opus: opus_datas.append(opus)
                )

            return opus_datas

        except Exception as e:
            logger.bind(tag=TAG).error(f"TTS请求异常: {e}")
            return []

