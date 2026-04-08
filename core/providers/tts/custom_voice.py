"""
自定义音色 TTS Provider
支持通过智能体配置的 instance_id、voice_model 和 voice_emotion 使用自定义音色
"""
import requests
from config.logger import setup_logging
from core.providers.tts.base import TTSProviderBase

TAG = __name__
logger = setup_logging()


class TTSProvider(TTSProviderBase):
    """
    支持自定义音色的 TTS Provider
    
    配置示例:
    TTS:
      custom_voice:
        type: custom_voice
        instance_id: "your-instance-id"  # 可选，优先使用智能体配置
        voice_model: "default.pt"  # 可选，优先使用智能体配置
        voice_emotion: "平静"  # 可选，优先使用智能体配置
        text_lang: "zh"  # 文本语言
        fallback_tts: "edge"  # 可选，失败时降级到的 TTS
    """
    
    def __init__(self, config, delete_audio_file):
        super().__init__(config, delete_audio_file)
        
        # TTS 实例配置（可被智能体配置覆盖）
        self.instance_id = config.get("instance_id")
        self.voice_model = config.get("voice_model", "default.pt")
        self.voice_emotion = config.get("voice_emotion", "平静")
        
        # 其他配置
        self.text_lang = config.get("text_lang", "zh")
        self.speed = float(config.get("speed", "1.0")) if config.get("speed") else 1.0
        self.audio_file_type = config.get("format", "wav")
        
        # 降级配置
        self.fallback_tts = config.get("fallback_tts")
        
        # 连接超时配置
        self.timeout = int(config.get("timeout", 30))
        
        logger.bind(tag=TAG).info(
            f"CustomVoice TTS 初始化: instance_id={self.instance_id}, "
            f"voice_model={self.voice_model}, voice_emotion={self.voice_emotion}"
        )
    
    def _get_tts_url(self, instance_id: str) -> str:
        """
        构建 TTS 服务 URL
        
        Args:
            instance_id: TTS 实例 ID
            
        Returns:
            TTS 服务 URL
        """
        return f"https://{instance_id}-9880.container.x-gpu.com/tts"
    
    def _get_effective_config(self):
        """
        获取有效的配置，智能体配置优先于默认配置
        
        Returns:
            (instance_id, voice_model, voice_emotion) 元组
        """
        instance_id = self.instance_id
        voice_model = self.voice_model
        voice_emotion = self.voice_emotion
        
        # 尝试从连接的配置获取音色设置
        if hasattr(self, 'conn') and self.conn:
            config = getattr(self.conn, 'config', None)
            if config:
                # 获取选中的 TTS 模块
                selected_module = config.get('selected_module', {})
                selected_tts = selected_module.get('TTS')
                
                if selected_tts:
                    tts_config = config.get('TTS', {}).get(selected_tts, {})
                    
                    # 优先使用智能体配置的音色设置
                    if tts_config.get('instance_id'):
                        instance_id = tts_config['instance_id']
                    if tts_config.get('voice_model'):
                        voice_model = tts_config['voice_model']
                    if tts_config.get('voice_emotion'):
                        voice_emotion = tts_config['voice_emotion']
                        
                    logger.bind(tag=TAG).debug(
                        f"使用智能体音色配置: instance_id={instance_id}, "
                        f"voice_model={voice_model}, voice_emotion={voice_emotion}"
                    )
        
        return instance_id, voice_model, voice_emotion
    
    async def text_to_speak(self, text, output_file):
        """
        将文本转换为语音
        
        Args:
            text: 要转换的文本
            output_file: 输出文件路径
            
        Returns:
            音频数据（如果 output_file 为 None）
        """
        instance_id, voice_model, voice_emotion = self._get_effective_config()
        
        if not instance_id:
            error_msg = "CustomVoice TTS: 未配置 instance_id"
            logger.bind(tag=TAG).error(error_msg)
            raise Exception(error_msg)
        
        # 构建请求
        url = self._get_tts_url(instance_id)
        request_data = {
            "text": text,
            "speaker": voice_model,  # 音色模型
            "emo": voice_emotion,  # 情感
            "lang": self.text_lang,
            "speed": self.speed
        }
        
        logger.bind(tag=TAG).debug(
            f"CustomVoice TTS 请求: url={url}, speaker={voice_model}, emo={voice_emotion}"
        )
        
        try:
            resp = requests.post(
                url, 
                json=request_data, 
                timeout=self.timeout,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "audio/wav"
                }
            )
            
            if resp.status_code == 200:
                if output_file:
                    with open(output_file, "wb") as file:
                        file.write(resp.content)
                else:
                    return resp.content
            else:
                error_msg = (
                    f"CustomVoice TTS 请求失败: {resp.status_code} - {resp.text}"
                )
                logger.bind(tag=TAG).error(error_msg)
                raise Exception(error_msg)
                
        except requests.exceptions.Timeout:
            error_msg = f"CustomVoice TTS 请求超时: instance_id={instance_id}"
            logger.bind(tag=TAG).error(error_msg)
            raise Exception(error_msg)
            
        except requests.exceptions.ConnectionError as e:
            error_msg = f"CustomVoice TTS 连接失败: instance_id={instance_id}, error={e}"
            logger.bind(tag=TAG).error(error_msg)
            raise Exception(error_msg)
            
        except Exception as e:
            error_msg = f"CustomVoice TTS 异常: {e}"
            logger.bind(tag=TAG).error(error_msg)
            raise
