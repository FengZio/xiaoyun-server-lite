"""
Mel-spectrogram 特征提取器
用于深度学习模型的输入预处理
"""

import librosa
import numpy as np
import logging
from typing import Optional, Tuple
import gc

logger = logging.getLogger(__name__)


class MelSpectrogramExtractor:
    """Mel频谱图提取器"""
    
    def __init__(
        self,
        sr: int = 22050,
        n_fft: int = 2048,
        hop_length: int = 512,
        n_mels: int = 128,
        duration: float = 3.0,
        offset: float = 30.0,
        target_shape: Tuple[int, int] = (128, 128)
    ):
        """
        初始化Mel频谱图提取器
        
        Args:
            sr: 采样率
            n_fft: FFT窗口大小
            hop_length: 帧移
            n_mels: Mel频带数量
            duration: 音频片段时长(秒)
            offset: 跳过前奏时间(秒)
            target_shape: 目标输出形状 (height, width)
        """
        self.sr = sr
        self.n_fft = n_fft
        self.hop_length = hop_length
        self.n_mels = n_mels
        self.duration = duration
        self.offset = offset
        self.target_shape = target_shape
        
    def extract(self, audio_path: str) -> Optional[np.ndarray]:
        """
        提取Mel频谱图
        
        Args:
            audio_path: 音频文件路径
            
        Returns:
            shape (n_mels, target_width) 的归一化Mel频谱图，失败返回None
        """
        try:
            logger.info(f"🎵 提取Mel频谱图: {audio_path}")
            
            # 1. 加载音频
            y, sr = librosa.load(
                audio_path,
                sr=self.sr,
                offset=self.offset,
                duration=self.duration
            )
            
            # 检查音频长度
            if len(y) == 0:
                logger.error(f"❌ 音频文件为空: {audio_path}")
                return None
                
            if len(y) < sr * 0.5:
                logger.error(f"❌ 音频文件太短 ({len(y)/sr:.2f}秒): {audio_path}")
                return None
            
            # 2. 计算Mel频谱图
            mel_spec = librosa.feature.melspectrogram(
                y=y,
                sr=sr,
                n_fft=self.n_fft,
                hop_length=self.hop_length,
                n_mels=self.n_mels,
                fmax=8000  # 限制最高频率
            )
            
            # 3. 转换为分贝刻度
            mel_spec_db = librosa.power_to_db(mel_spec, ref=np.max)
            
            # 4. Resize到目标形状
            mel_spec_resized = self._resize_spectrogram(
                mel_spec_db,
                self.target_shape
            )
            
            # 5. 归一化到 [0, 1]
            mel_spec_normalized = self._normalize(mel_spec_resized)
            
            # 6. 清理内存
            del y, mel_spec, mel_spec_db
            gc.collect()
            
            logger.info(f"✅ Mel频谱图提取成功: {mel_spec_normalized.shape}")
            return mel_spec_normalized
            
        except Exception as e:
            logger.error(f"❌ Mel频谱图提取失败: {audio_path}, 错误: {e}")
            return None
    
    def _resize_spectrogram(
        self,
        spec: np.ndarray,
        target_shape: Tuple[int, int]
    ) -> np.ndarray:
        """
        调整频谱图大小
        
        Args:
            spec: 输入频谱图 (n_mels, time_steps)
            target_shape: 目标形状 (height, width)
            
        Returns:
            调整后的频谱图
        """
        from scipy.ndimage import zoom
        
        # 计算缩放因子
        h_factor = target_shape[0] / spec.shape[0]
        w_factor = target_shape[1] / spec.shape[1]
        
        # 使用双线性插值resize
        spec_resized = zoom(spec, (h_factor, w_factor), order=1)
        
        return spec_resized
    
    def _normalize(self, spec: np.ndarray) -> np.ndarray:
        """
        归一化频谱图到 [0, 1]
        
        Args:
            spec: 输入频谱图
            
        Returns:
            归一化后的频谱图
        """
        spec_min = spec.min()
        spec_max = spec.max()
        
        if spec_max - spec_min < 1e-8:
            # 避免除零
            return np.zeros_like(spec)
        
        spec_normalized = (spec - spec_min) / (spec_max - spec_min)
        return spec_normalized
    
    def extract_with_augmentation(
        self,
        audio_path: str,
        augment: bool = False
    ) -> Optional[np.ndarray]:
        """
        提取Mel频谱图，支持数据增强
        
        Args:
            audio_path: 音频文件路径
            augment: 是否应用数据增强
            
        Returns:
            Mel频谱图
        """
        if not augment:
            return self.extract(audio_path)
        
        # 随机选择增强方法
        import random
        augmentation_type = random.choice(['time_stretch', 'pitch_shift', 'noise', 'none'])
        
        try:
            # 加载音频
            y, sr = librosa.load(
                audio_path,
                sr=self.sr,
                offset=self.offset,
                duration=self.duration
            )
            
            # 检查音频长度
            if len(y) == 0:
                logger.error(f"❌ 音频文件为空: {audio_path}")
                return None
                
            if len(y) < sr * 0.5:
                logger.error(f"❌ 音频文件太短 ({len(y)/sr:.2f}秒): {audio_path}")
                return None
            
            # 应用增强
            if augmentation_type == 'time_stretch':
                # 时间拉伸 (0.9-1.1倍速)
                rate = random.uniform(0.9, 1.1)
                y = librosa.effects.time_stretch(y, rate=rate)
                logger.debug(f"应用时间拉伸: rate={rate:.2f}")
                
            elif augmentation_type == 'pitch_shift':
                # 音高变换 (-2到+2半音)
                n_steps = random.uniform(-2, 2)
                y = librosa.effects.pitch_shift(y, sr=sr, n_steps=n_steps)
                logger.debug(f"应用音高变换: n_steps={n_steps:.2f}")
                
            elif augmentation_type == 'noise':
                # 添加随机噪声
                noise_factor = random.uniform(0.001, 0.005)
                noise = np.random.randn(len(y)) * noise_factor
                y = y + noise
                logger.debug(f"添加噪声: factor={noise_factor:.4f}")
            
            # 计算Mel频谱图
            mel_spec = librosa.feature.melspectrogram(
                y=y,
                sr=sr,
                n_fft=self.n_fft,
                hop_length=self.hop_length,
                n_mels=self.n_mels,
                fmax=8000
            )
            
            mel_spec_db = librosa.power_to_db(mel_spec, ref=np.max)
            mel_spec_resized = self._resize_spectrogram(mel_spec_db, self.target_shape)
            mel_spec_normalized = self._normalize(mel_spec_resized)
            
            # 清理内存
            del y, mel_spec, mel_spec_db
            gc.collect()
            
            return mel_spec_normalized
            
        except Exception as e:
            logger.error(f"❌ 增强提取失败: {audio_path}, 错误: {e}")
            return None  # 返回None而不是降级，避免无限循环


# 便捷函数
def extract_mel_spectrogram(
    audio_path: str,
    target_shape: Tuple[int, int] = (128, 128)
) -> Optional[np.ndarray]:
    """
    便捷函数：提取Mel频谱图
    
    Args:
        audio_path: 音频文件路径
        target_shape: 目标形状
        
    Returns:
        Mel频谱图 or None
    """
    extractor = MelSpectrogramExtractor(target_shape=target_shape)
    return extractor.extract(audio_path)


if __name__ == '__main__':
    # 测试代码
    logging.basicConfig(level=logging.INFO)
    
    print("=" * 60)
    print("Mel频谱图提取器")
    print("=" * 60)
    print(f"默认配置:")
    print(f"  - 采样率: 22050 Hz")
    print(f"  - Mel频带数: 128")
    print(f"  - 输出形状: (128, 128)")
    print(f"  - 音频片段: 3秒 (从30秒开始)")
    print("=" * 60)
