#!/usr/bin/env python3
"""
Server A 增强版音频分析Worker
整合 MobileNet + MFCC 情感分析
部署在 APP服务器 (4核4G)，内存占用 < 350MB
"""

import os
import sys
import json
import time
import redis
import requests
import librosa
import numpy as np
import tempfile
import logging
from datetime import datetime
from typing import Dict, Any, Optional, Tuple
from concurrent.futures import ProcessPoolExecutor, TimeoutError
import psutil
import signal
import gc

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 导入轻量级推理引擎
from lite_inference import LiteEmotionPredictor

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class EnhancedAudioAnalysisWorker:
    """增强版音频分析Worker - 整合MobileNet情感分析"""
    
    def __init__(self):
        # Server B API配置
        # Server B API 配置（优先读 config.py，回退环境变量）
        try:
            from config import SERVER_B_URL, INTERNAL_TOKEN
            self.server_b_url = SERVER_B_URL
            self.internal_token = INTERNAL_TOKEN
        except ImportError:
            self.server_b_url = os.environ.get('SERVER_B_URL', 'https://tag.zrj666.cn')
            self.internal_token = os.environ.get('INTERNAL_TOKEN', '')
        
        # 分析配置
        self.max_workers = 1  # 降低并发以控制内存
        self.download_timeout = 30
        self.analysis_timeout = 120
        self.temp_dir = '/tmp/audio_analysis'
        
        # 设置环境变量
        os.environ['TMPDIR'] = self.temp_dir
        os.environ['TEMP'] = self.temp_dir
        os.environ['TMP'] = self.temp_dir
        os.environ['NUMBA_CACHE_DIR'] = self.temp_dir
        os.environ['NUMBA_DISABLE_PERFORMANCE_WARNINGS'] = '1'
        
        # 创建临时目录
        os.makedirs(self.temp_dir, exist_ok=True)
        
        # 加载MobileNet模型（量化版本）
        self.emotion_predictor = None
        self.load_emotion_model()
        
        # 运行状态
        self.running = True
        self.current_tasks = set()
        
        # 注册信号处理
        signal.signal(signal.SIGTERM, self.signal_handler)
        signal.signal(signal.SIGINT, self.signal_handler)
        
        logger.info("✅ 增强版Worker初始化完成")
    
    def load_emotion_model(self):
        """加载情感分析模型"""
        try:
            # 优先使用量化模型
            model_paths = [
                'models/model_mobile.ptl'
            ]
            
            model_path = None
            for path in model_paths:
                if os.path.exists(path):
                    model_path = path
                    logger.info(f"找到模型: {path}")
                    break
            
            if model_path:
                self.emotion_predictor = LiteEmotionPredictor(model_path, device='cpu')
                memory_mb = self.emotion_predictor.get_memory_usage()
                logger.info(f"✅ MobileNet模型加载成功，内存占用: {memory_mb:.2f} MB")
            else:
                logger.warning("⚠️ 未找到MobileNet模型，将使用基础MFCC分析")
                self.emotion_predictor = None
                
        except Exception as e:
            logger.error(f"❌ 模型加载失败: {e}")
            self.emotion_predictor = None
    
    def signal_handler(self, signum, frame):
        """信号处理器"""
        logger.info(f"收到信号 {signum}，准备停止Worker...")
        self.running = False
    
    def check_memory_usage(self) -> bool:
        """检查内存使用情况"""
        try:
            memory = psutil.virtual_memory()
            memory_percent = memory.percent
            memory_mb = memory.used / (1024 * 1024)
            
            logger.debug(f"内存使用: {memory_percent:.1f}% ({memory_mb:.0f} MB)")
            
            # 如果内存使用超过80%，暂停接收新任务
            if memory_percent > 80:
                logger.warning(f"⚠️ 内存使用过高: {memory_percent:.1f}%，暂停接收新任务")
                # 强制垃圾回收
                gc.collect()
                return False
            
            return True
        except Exception as e:
            logger.error(f"检查内存失败: {e}")
            return True
    
    def download_audio(self, url: str, music_id: str) -> Optional[str]:
        """下载音频文件"""
        try:
            logger.info(f"📥 开始下载音频: {music_id}")
            
            temp_file = os.path.join(self.temp_dir, f"{music_id}_{int(time.time())}.tmp")
            
            response = requests.get(
                url, 
                timeout=self.download_timeout,
                stream=True,
                headers={'User-Agent': 'EnhancedAudioAnalysisWorker/2.0'}
            )
            response.raise_for_status()
            
            with open(temp_file, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            
            file_size = os.path.getsize(temp_file)
            logger.info(f"✅ 音频下载完成: {music_id}, 大小: {file_size / 1024 / 1024:.2f}MB")
            
            return temp_file
            
        except Exception as e:
            logger.error(f"❌ 下载失败: {music_id}, 错误: {e}")
            return None
    
    def analyze_audio_with_mobilenet(self, file_path: str, music_id: str) -> Optional[Dict[str, Any]]:
        """使用MobileNet进行情感分析"""
        try:
            if not self.emotion_predictor:
                return None
            
            logger.info(f"🤖 MobileNet情感分析: {music_id}")
            start_time = time.time()
            
            # 使用MobileNet预测
            result = self.emotion_predictor.predict(file_path)
            
            if result:
                processing_time = time.time() - start_time
                logger.info(f"✅ MobileNet分析完成: {music_id}, 耗时: {processing_time:.2f}s")

                predictions = result.get('predictions') or {}
                if predictions:
                    confidence = self.estimate_prediction_confidence(predictions, method='mobilenet')
                    predictions['confidence'] = confidence
                    predictions['algorithm_version'] = 'mobilenet_v1.0'
                    predictions['legacy_style_dimensions'] = {
                        'openness': predictions.get('openness'),
                        'extraversion': predictions.get('extraversion'),
                        'agreeableness': predictions.get('agreeableness'),
                    }
                
                # 添加元数据
                result['metadata'] = {
                    'model': 'MobileNetV3-Quantized',
                    'processing_time': processing_time,
                    'algorithm_version': 'mobilenet_v1.0'
                }
                
                return result
            else:
                logger.warning(f"⚠️ MobileNet分析失败: {music_id}")
                return None
                
        except Exception as e:
            logger.error(f"❌ MobileNet分析异常: {music_id}, 错误: {e}")
            return None
    
    def analyze_audio_with_mfcc(self, file_path: str, music_id: str) -> Optional[Dict[str, Any]]:
        """使用MFCC进行基础情感分析（备用方案）"""
        try:
            logger.info(f"🎵 MFCC情感分析: {music_id}")
            start_time = time.time()
            
            # 加载音频
            y, sr = librosa.load(file_path, sr=22050)
            
            # BPM分析
            tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
            
            # 频谱特征
            spectral_centroids = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
            spectral_rolloff = librosa.feature.spectral_rolloff(y=y, sr=sr)[0]
            zero_crossing_rate = librosa.feature.zero_crossing_rate(y)[0]
            
            # MFCC特征
            mfccs = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13)
            
            # 情感分析
            emotion_data = self.analyze_emotion_from_features(
                tempo, spectral_centroids, spectral_rolloff, 
                zero_crossing_rate, mfccs
            )
            emotion_data['confidence'] = self.estimate_prediction_confidence(emotion_data, method='mfcc')
            emotion_data['algorithm_version'] = 'mfcc_v1.0'
            emotion_data['legacy_style_dimensions'] = {
                'openness': emotion_data.get('openness'),
                'extraversion': emotion_data.get('extraversion'),
                'agreeableness': emotion_data.get('agreeableness'),
            }
            
            processing_time = time.time() - start_time
            
            result = {
                'predictions': emotion_data,
                'interpretation': self.interpret_emotions(emotion_data),
                'metadata': {
                    'model': 'MFCC-Basic',
                    'processing_time': processing_time,
                    'algorithm_version': 'mfcc_v1.0',
                    'bpm': float(tempo)
                }
            }
            
            logger.info(f"✅ MFCC分析完成: {music_id}, 耗时: {processing_time:.2f}s")
            return result
            
        except Exception as e:
            logger.error(f"❌ MFCC分析失败: {music_id}, 错误: {e}")
            return None
    
    @staticmethod
    def analyze_emotion_from_features(tempo: float, spectral_centroids: np.ndarray,
                                     spectral_rolloff: np.ndarray, zcr: np.ndarray,
                                     mfccs: np.ndarray) -> Dict[str, float]:
        """基于音频特征的情感分析"""
        try:
            sc_mean = np.mean(spectral_centroids)
            sr_mean = np.mean(spectral_rolloff)
            zcr_mean = np.mean(zcr)
            mfcc_mean = np.mean(mfccs, axis=1)
            
            # 情感映射
            valence = np.tanh((sc_mean - 1500) / 1000) * 0.7
            valence = max(-1.0, min(1.0, valence))
            
            arousal = np.tanh((tempo - 100) / 50) * 0.8
            arousal = max(-1.0, min(1.0, arousal))
            
            openness = np.tanh((sr_mean - 3000) / 2000) * 0.6
            openness = max(-1.0, min(1.0, openness))
            
            extraversion = np.tanh(zcr_mean * 100 - 5) * 0.5
            extraversion = max(-1.0, min(1.0, extraversion))
            
            agreeableness = np.tanh(np.mean(mfcc_mean[1:5]) / 10) * 0.4
            agreeableness = max(-1.0, min(1.0, agreeableness))
            
            return {
                'valence': float(valence),
                'arousal': float(arousal),
                'openness': float(openness),
                'extraversion': float(extraversion),
                'agreeableness': float(agreeableness)
            }
            
        except Exception as e:
            logger.error(f"情感分析失败: {e}")
            return {
                'valence': 0.0,
                'arousal': 0.0,
                'openness': 0.0,
                'extraversion': 0.0,
                'agreeableness': 0.0
            }

    @staticmethod
    def estimate_prediction_confidence(predictions: Dict[str, Any], method: str = 'mobilenet') -> float:
        """估计情感预测置信度（0-1），用于下游融合加权。"""
        try:
            dims = ['valence', 'arousal', 'openness', 'extraversion', 'agreeableness']
            values = []
            for key in dims:
                value = predictions.get(key)
                if value is None:
                    continue
                values.append(float(value))

            if not values:
                return 0.5

            arr = np.array(values, dtype=np.float32)
            intensity = float(np.mean(np.abs(arr)))      # 平均强度越高，通常信号更明确
            spread = float(np.std(arr))                  # 维度分布适中时更可信

            intensity_score = min(1.0, intensity / 0.8)
            spread_score = max(0.0, 1.0 - abs(spread - 0.35) / 0.35)
            base_score = 0.6 * intensity_score + 0.4 * spread_score

            # MobileNet整体可靠性更高，MFCC作为备选略降
            if method == 'mobilenet':
                calibrated = 0.65 + 0.30 * base_score
            else:
                calibrated = 0.45 + 0.30 * base_score

            return round(float(max(0.0, min(1.0, calibrated))), 4)
        except Exception:
            return 0.7 if method == 'mobilenet' else 0.55
    
    @staticmethod
    def interpret_emotions(predictions: Dict[str, float]) -> Dict[str, Any]:
        """解释歌曲层情感标签，不代表用户当前状态。"""
        valence = predictions.get('valence', 0.0)
        arousal = predictions.get('arousal', 0.0)
        
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
        
        return {
            "scope": "song_signal_only",
            "mood": mood,
            "valence_label": "积极" if valence > 0 else "消极",
            "arousal_label": "激动" if arousal > 0 else "平静"
        }
    
    def analyze_audio_complete(self, file_path: str, music_id: str) -> Optional[Dict[str, Any]]:
        """完整的音频分析（MobileNet + Librosa）"""
        try:
            start_time = time.time()
            logger.info(f"🎵 开始完整分析: {music_id}")
            
            # 1. MobileNet情感分析
            mobilenet_result = None
            if self.emotion_predictor:
                mobilenet_result = self.analyze_audio_with_mobilenet(file_path, music_id)
            
            # 2. Librosa基础分析（BPM、波形等）
            y, sr = librosa.load(file_path, sr=22050)
            duration = librosa.get_duration(y=y, sr=sr)
            
            # BPM
            tempo, beats = librosa.beat.beat_track(y=y, sr=sr)
            
            # 波形数据（降采样）
            hop_length = 512
            frame_length = 2048
            n_frames = len(y) // hop_length
            
            waveform_points = []
            time_points = []
            step = max(1, n_frames // 1000)
            
            for i in range(0, n_frames, step):
                start_sample = i * hop_length
                end_sample = min(start_sample + frame_length, len(y))
                if end_sample > start_sample:
                    amplitude = np.mean(np.abs(y[start_sample:end_sample]))
                    waveform_points.append(float(amplitude))
                    time_points.append(float(i * hop_length / sr))
            
            # 3. 如果MobileNet失败，使用MFCC备用
            if not mobilenet_result:
                logger.info("使用MFCC备用分析")
                mobilenet_result = self.analyze_audio_with_mfcc(file_path, music_id)
            
            # 4. 整合结果
            processing_time = time.time() - start_time
            
            analysis_result = {
                'music_id': music_id,
                'bpm': float(tempo),
                'waveform_data': {
                    'amplitude': waveform_points,
                    'time_axis': time_points,
                    'sample_rate': sr,
                    'duration': float(duration)
                },
                'emotion_data': mobilenet_result.get('predictions', {}) if mobilenet_result else {},
                'interpretation': mobilenet_result.get('interpretation', {}) if mobilenet_result else {},
                'metadata': {
                    'processing_time': processing_time,
                    'server_info': 'app-server-4c4g-enhanced',
                    'analysis_date': datetime.now().isoformat(),
                    'file_size': os.path.getsize(file_path),
                    'model_used': mobilenet_result.get('metadata', {}).get('model', 'unknown') if mobilenet_result else 'mfcc',
                    'librosa_version': librosa.__version__
                }
            }
            
            # 清理内存
            del y
            gc.collect()
            
            logger.info(f"✅ 完整分析完成: {music_id}, BPM: {tempo:.1f}, 耗时: {processing_time:.2f}s")
            return analysis_result
            
        except Exception as e:
            logger.error(f"❌ 完整分析失败: {music_id}, 错误: {e}")
            return None
    
    def send_result_to_server_b(self, result: Dict[str, Any]) -> bool:
        """发送分析结果到Server B"""
        try:
            url = f"{self.server_b_url}/internal/save-analysis"
            headers = {
                'Content-Type': 'application/json',
                'X-Internal-Token': self.internal_token
            }
            
            response = requests.post(
                url, 
                json=result, 
                headers=headers, 
                timeout=10
            )
            
            if response.status_code == 200:
                logger.info(f"✅ 结果已发送到Server B: {result['music_id']}")
                return True
            else:
                logger.error(f"❌ 发送结果失败: {response.status_code}")
                return False
                
        except Exception as e:
            logger.error(f"❌ 发送结果异常: {e}")
            return False
    
    def cleanup_file(self, file_path: str):
        """清理临时文件"""
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                logger.debug(f"🗑️ 清理临时文件: {file_path}")
        except Exception as e:
            logger.warning(f"清理文件失败: {file_path}, 错误: {e}")
    
    def get_task_from_api(self) -> Optional[Dict[str, Any]]:
        """通过HTTP API从Server B获取任务"""
        try:
            response = requests.get(
                f"{self.server_b_url}/internal/get-task",
                headers={'X-Internal-Token': self.internal_token},
                timeout=10
            )
            
            if response.status_code == 200:
                result = response.json()
                if result.get('success') and result.get('task'):
                    return result['task']
            elif response.status_code != 404:
                logger.error(f"❌ 获取任务失败: HTTP {response.status_code}")
                
        except Exception as e:
            logger.error(f"❌ API请求失败: {e}")
            
        return None
    
    def process_task(self, task_data: Dict[str, Any]) -> bool:
        """处理单个分析任务"""
        music_id = task_data.get('music_id')
        url = task_data.get('url')
        
        if not music_id or not url:
            logger.error("❌ 任务数据不完整")
            return False
        
        temp_file = None
        
        try:
            # 下载音频
            temp_file = self.download_audio(url, music_id)
            if not temp_file:
                return False
            
            # 完整分析
            result = self.analyze_audio_complete(temp_file, music_id)
            if not result:
                return False
            
            # 发送结果
            success = self.send_result_to_server_b(result)
            
            if success:
                logger.info(f"🎉 任务完成: {music_id}")
                
                # 显示内存使用
                memory_mb = psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
                logger.info(f"📊 当前内存占用: {memory_mb:.2f} MB")
                
                return True
            else:
                return False
            
        except Exception as e:
            logger.error(f"❌ 处理任务异常: {music_id}, 错误: {e}")
            return False
        
        finally:
            # 清理资源
            if temp_file:
                self.cleanup_file(temp_file)
            
            self.current_tasks.discard(music_id)
            
            # 强制垃圾回收
            gc.collect()
    
    def run_worker_loop(self):
        """运行Worker主循环"""
        logger.info("=" * 60)
        logger.info("🚀 启动增强版音频分析Worker")
        logger.info("=" * 60)
        logger.info(f"MobileNet模型: {'已加载' if self.emotion_predictor else '未加载（使用MFCC）'}")
        logger.info(f"最大并发: {self.max_workers}")
        logger.info("=" * 60)
        
        while self.running:
            try:
                # 检查内存
                if not self.check_memory_usage():
                    time.sleep(60)
                    continue
                
                # 检查任务数
                if len(self.current_tasks) >= self.max_workers:
                    time.sleep(5)
                    continue
                
                # 获取任务
                task_data = self.get_task_from_api()
                
                if not task_data:
                    time.sleep(5)
                    continue
                
                music_id = task_data.get('music_id')
                self.current_tasks.add(music_id)
                
                logger.info(f"📋 接收到任务: {music_id} - {task_data.get('title', 'Unknown')}")
                
                # 处理任务
                self.process_task(task_data)
                
            except KeyboardInterrupt:
                logger.info("👋 Worker停止")
                break
            except Exception as e:
                logger.error(f"❌ Worker循环异常: {e}")
                time.sleep(30)
        
        logger.info("🏁 Worker已停止")


if __name__ == "__main__":
    # 启动增强版Worker
    worker = EnhancedAudioAnalysisWorker()
    worker.run_worker_loop()
