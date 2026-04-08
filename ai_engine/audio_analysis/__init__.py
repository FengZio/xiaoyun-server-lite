"""
Audio Analysis Module
音频情感分析模块
"""

from .lite_inference import LiteEmotionPredictor
from .spectrogram_extractor import MelSpectrogramExtractor

__all__ = [
    'LiteEmotionPredictor',
    'MelSpectrogramExtractor',
]
