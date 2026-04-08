"""
统一音频分析接口
整合 MobileNet 和 MFCC 分析
"""

import logging
from typing import Optional, Dict
from pathlib import Path
import numpy as np

logger = logging.getLogger(__name__)

# 延迟导入,避免启动时加载重型库
_predictor = None
_librosa = None


def _get_predictor(model_path: str):
    """延迟加载 MobileNet 预测器"""
    global _predictor
    if _predictor is None:
        from .lite_inference import LiteEmotionPredictor
        _predictor = LiteEmotionPredictor(model_path, device='cpu')
    return _predictor


def _get_librosa():
    """延迟导入 librosa"""
    global _librosa
    if _librosa is None:
        import librosa
        _librosa = librosa
    return _librosa


class AudioEmotionAnalyzer:
    """音频情感分析器"""
    
    def __init__(self, model_path: Optional[str] = None):
        """
        初始化分析器
        
        Args:
            model_path: MobileNet 模型路径,None则仅使用MFCC
        """
        self.model_path = model_path
        self.use_mobilenet = model_path and Path(model_path).exists()
        
        if self.use_mobilenet:
            logger.info(f"✅ MobileNet 模式: {model_path}")
        else:
            logger.info("📊 使用 MFCC 基础分析")
    
    def analyze(self, audio_path: str) -> Optional[Dict]:
        """
        分析音频情感
        
        Args:
            audio_path: 音频文件路径 (支持 mp3, wav, flac 等)
            
        Returns:
            {
                'predictions': {
                    'valence': float,
                    'arousal': float,
                    'openness': float,
                    'extraversion': float,
                    'agreeableness': float
                },
                'interpretation': {
                    'mood': str,
                    'valence_label': str,
                    'arousal_label': str
                },
                'metadata': {
                    'model': str,
                    'source': str
                }
            }
        """
        try:
            # 优先使用 MobileNet
            if self.use_mobilenet:
                predictor = _get_predictor(self.model_path)
                result = predictor.predict(audio_path)
                if result:
                    result['metadata'] = {
                        'model': 'MobileNetV3-Quantized',
                        'source': 'deep_learning'
                    }
                    return result
            
            # 备用: MFCC 分析
            logger.info("使用 MFCC 备用分析")
            return self._analyze_with_mfcc(audio_path)
            
        except Exception as e:
            logger.error(f"❌ 音频分析失败: {e}")
            return None
    
    def _analyze_with_mfcc(self, audio_path: str) -> Optional[Dict]:
        """MFCC 备用分析"""
        try:
            librosa = _get_librosa()
            
            # 加载音频
            y, sr = librosa.load(audio_path, sr=22050)
            
            # 提取特征
            tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
            spectral_centroids = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
            spectral_rolloff = librosa.feature.spectral_rolloff(y=y, sr=sr)[0]
            zcr = librosa.feature.zero_crossing_rate(y)[0]
            mfccs = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13)
            
            # 情感计算
            sc_mean = np.mean(spectral_centroids)
            sr_mean = np.mean(spectral_rolloff)
            zcr_mean = np.mean(zcr)
            mfcc_mean = np.mean(mfccs, axis=1)
            
            valence = np.tanh((sc_mean - 1500) / 1000) * 0.7
            arousal = np.tanh((tempo - 100) / 50) * 0.8
            openness = np.tanh((sr_mean - 3000) / 2000) * 0.6
            extraversion = np.tanh(zcr_mean * 100 - 5) * 0.5
            agreeableness = np.tanh(np.mean(mfcc_mean[1:5]) / 10) * 0.4
            
            predictions = {
                'valence': float(np.clip(valence, -1, 1)),
                'arousal': float(np.clip(arousal, -1, 1)),
                'openness': float(np.clip(openness, -1, 1)),
                'extraversion': float(np.clip(extraversion, -1, 1)),
                'agreeableness': float(np.clip(agreeableness, -1, 1))
            }
            
            # 解释
            mood = self._interpret_mood(predictions['valence'], predictions['arousal'])
            
            return {
                'predictions': predictions,
                'interpretation': {
                    'mood': mood,
                    'valence_label': '积极' if predictions['valence'] > 0 else '消极',
                    'arousal_label': '激动' if predictions['arousal'] > 0 else '平静'
                },
                'metadata': {
                    'model': 'MFCC-Basic',
                    'source': 'traditional',
                    'bpm': float(tempo)
                }
            }
            
        except Exception as e:
            logger.error(f"❌ MFCC分析失败: {e}")
            return None
    
    @staticmethod
    def _interpret_mood(valence: float, arousal: float) -> str:
        """情绪象限判定"""
        if valence > 0.1 and arousal > 0.1:
            return "快乐激动"
        elif valence > 0.1 and arousal < -0.1:
            return "平静愉悦"
        elif valence < -0.1 and arousal > 0.1:
            return "愤怒紧张"
        elif valence < -0.1 and arousal < -0.1:
            return "悲伤低落"
        else:
            return "中性平静"


# 便捷函数
def analyze_audio_emotion(audio_path: str, model_path: Optional[str] = None) -> Optional[Dict]:
    """
    便捷函数: 分析音频情感
    
    Args:
        audio_path: 音频文件路径
        model_path: MobileNet 模型路径 (可选)
        
    Returns:
        情感分析结果字典
    """
    analyzer = AudioEmotionAnalyzer(model_path)
    return analyzer.analyze(audio_path)
