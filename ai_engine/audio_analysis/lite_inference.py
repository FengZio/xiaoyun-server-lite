"""
轻量级推理引擎
使用量化后的 PyTorch Mobile 模型进行推理
内存占用 < 350MB
"""

import torch
import numpy as np
import logging
from pathlib import Path
from typing import Optional, Dict
import gc

from spectrogram_extractor import MelSpectrogramExtractor

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class LiteEmotionPredictor:
    """轻量级情感预测器"""
    
    def __init__(self, model_path: str, device: str = 'cpu'):
        """
        初始化预测器
        
        Args:
            model_path: 量化模型路径 (.ptl 或 .pt)
            device: 设备 (只支持 'cpu')
        """
        self.device = device
        self.model = None
        self.extractor = None
        self.dimension_names = ['valence', 'arousal', 'openness', 'extraversion', 'agreeableness']
        
        # 加载模型
        self._load_model(model_path)
        
        # 初始化频谱图提取器
        self.extractor = MelSpectrogramExtractor(target_shape=(128, 128))
        
        logger.info("✅ 轻量级预测器初始化完成")
    
    def _load_model(self, model_path: str):
        """加载量化模型"""
        if not Path(model_path).exists():
            raise FileNotFoundError(f"模型文件不存在: {model_path}")
        
        logger.info(f"加载模型: {model_path}")
        
        try:
            # 尝试加载 PyTorch Mobile 模型
            if model_path.endswith('.ptl'):
                self.model = torch.jit.load(model_path, map_location=self.device)
                logger.info("✅ 加载 PyTorch Mobile 模型")
            # 尝试加载 TorchScript 模型
            elif model_path.endswith('.pt'):
                self.model = torch.jit.load(model_path, map_location=self.device)
                logger.info("✅ 加载 TorchScript 模型")
            # 尝试加载量化的 PyTorch 模型
            else:
                from models.mobilenet_predictor import create_model
                self.model = create_model(pretrained=False, device=self.device)
                checkpoint = torch.load(model_path, map_location=self.device)
                self.model.load_state_dict(checkpoint)
                logger.info("✅ 加载量化 PyTorch 模型")
            
            self.model.eval()
            
            # 获取模型大小
            model_size = Path(model_path).stat().st_size / (1024 * 1024)
            logger.info(f"模型大小: {model_size:.2f} MB")
            
        except Exception as e:
            logger.error(f"模型加载失败: {e}")
            raise
    
    def predict(self, audio_path: str) -> Optional[Dict]:
        """
        预测音频情感
        
        Args:
            audio_path: 音频文件路径
            
        Returns:
            预测结果字典
        """
        try:
            # 提取频谱图
            spectrogram = self.extractor.extract(audio_path)
            
            if spectrogram is None:
                logger.error(f"频谱图提取失败: {audio_path}")
                return None
            
            # 转换为张量
            spec_tensor = torch.from_numpy(spectrogram).unsqueeze(0).unsqueeze(0).float()
            spec_tensor = spec_tensor.to(self.device)
            
            # 预测
            with torch.no_grad():
                predictions = self.model(spec_tensor)
                
                # 处理不同类型的输出
                if isinstance(predictions, torch.Tensor):
                    predictions = predictions.cpu().numpy()[0]
                else:
                    predictions = predictions[0].cpu().numpy()
            
            # 构建结果
            result = {
                'predictions': {
                    name: float(pred)
                    for name, pred in zip(self.dimension_names, predictions)
                },
                'interpretation': self._interpret_emotions(predictions)
            }
            
            # 清理内存
            del spec_tensor
            gc.collect()
            
            return result
            
        except Exception as e:
            logger.error(f"预测失败: {e}")
            return None
    
    def _interpret_emotions(self, predictions: np.ndarray) -> Dict:
        """解释情感预测结果"""
        valence, arousal, openness, extraversion, agreeableness = predictions
        
        # 情绪象限
        if valence > 0.1 and arousal > 0.1:
            mood = "快乐激动"
        elif valence > 0.1 and arousal < -0.1:
            mood = "平静愉悦"
        elif valence < -0.1 and arousal > 0.1:
            mood = "愤怒紧张"
        elif valence < -0.1 and arousal < -0.1:
            mood = "悲伤低落"
        else:
            mood = "中性平静"
        
        # 情感强度
        emotion_intensity = np.sqrt(valence**2 + arousal**2)
        
        # 人格特质
        personality = []
        if openness > 0.3:
            personality.append("复杂多变")
        if extraversion > 0.3:
            personality.append("外向活跃")
        if agreeableness > 0.3:
            personality.append("和谐宜人")
        
        return {
            "mood": mood,
            "valence_label": "积极" if valence > 0 else "消极",
            "arousal_label": "激动" if arousal > 0 else "平静",
            "personality": personality if personality else ["中性"],
            "emotion_score": float(emotion_intensity)
        }
    
    def get_memory_usage(self) -> float:
        """获取当前内存占用（MB）"""
        import psutil
        import os
        
        process = psutil.Process(os.getpid())
        memory_mb = process.memory_info().rss / (1024 * 1024)
        return memory_mb


def benchmark_inference(predictor: LiteEmotionPredictor, audio_path: str, num_runs: int = 10):
    """性能基准测试"""
    import time
    
    logger.info("=" * 60)
    logger.info("性能基准测试")
    logger.info("=" * 60)
    
    # 预热
    logger.info("预热...")
    for _ in range(3):
        predictor.predict(audio_path)
    
    # 测试
    logger.info(f"运行 {num_runs} 次推理...")
    times = []
    
    for i in range(num_runs):
        start = time.time()
        result = predictor.predict(audio_path)
        end = time.time()
        
        if result:
            times.append(end - start)
    
    # 统计
    if times:
        avg_time = np.mean(times)
        min_time = np.min(times)
        max_time = np.max(times)
        
        logger.info(f"\n推理时间统计:")
        logger.info(f"  平均: {avg_time*1000:.2f} ms")
        logger.info(f"  最小: {min_time*1000:.2f} ms")
        logger.info(f"  最大: {max_time*1000:.2f} ms")
        logger.info(f"  吞吐量: {1/avg_time:.2f} 次/秒")
    
    # 内存占用
    memory_mb = predictor.get_memory_usage()
    logger.info(f"\n内存占用: {memory_mb:.2f} MB")
    
    if memory_mb < 350:
        logger.info("✅ 内存占用符合要求 (< 350 MB)")
    else:
        logger.warning(f"⚠️  内存占用超标: {memory_mb:.2f} MB > 350 MB")
    
    logger.info("=" * 60)


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='轻量级情感预测')
    parser.add_argument('--model', type=str,
                        default='models/mobilenet_quantized/model_mobile.ptl',
                        help='量化模型路径')
    parser.add_argument('--audio', type=str,
                        help='音频文件路径')
    parser.add_argument('--benchmark', action='store_true',
                        help='运行性能测试')
    
    args = parser.parse_args()
    
    # 创建预测器
    predictor = LiteEmotionPredictor(args.model)
    
    if args.audio:
        # 预测
        logger.info(f"\n预测音频: {args.audio}")
        result = predictor.predict(args.audio)
        
        if result:
            logger.info("\n预测结果:")
            for dim, value in result['predictions'].items():
                logger.info(f"  {dim:15s}: {value:+.4f}")
            
            logger.info("\n情感解释:")
            interp = result['interpretation']
            logger.info(f"  情绪: {interp['mood']}")
            logger.info(f"  效价: {interp['valence_label']}")
            logger.info(f"  唤醒: {interp['arousal_label']}")
            logger.info(f"  人格: {', '.join(interp['personality'])}")
        
        # 性能测试
        if args.benchmark:
            benchmark_inference(predictor, args.audio)
    else:
        logger.info("请使用 --audio 参数指定音频文件")
        logger.info("示例: python lite_inference.py --model model.ptl --audio test.mp3 --benchmark")
