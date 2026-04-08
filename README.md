# xiaoyun-lite

轻量级 AI 对话服务器，项目源自 `xiaozhi-server`，在此基础上演进为 `xiaoyun-lite` 并整合音频情感分析。

## ✨ 特性

- ✅ WebSocket 实时通信
- ✅ AI Provider 架构 (ASR, TTS, LLM, Memory)
- ✅ 对话管理和上下文记忆
- ✅ 插件系统 (Function Calling)
- ✅ **MobileNet 音频情感分析** (量化版3MB)
- ✅ UniApp 客户端支持
- ✅ Server B 画像集成
- ❌ 移除 ESP32 硬件支持
- ❌ 移除 OTA 固件服务

## 🚀 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置

```bash
# 复制配置模板
copy config.yaml.template config.yaml

# 编辑配置文件
notepad config.yaml
```

必须配置项:
- `server.auth_key` - 认证密钥
- `selected_module.LLM` - LLM 服务商
- AI Provider 的 API 密钥

### 3. 启动服务

```bash
python app.py
```

服务将启动在:
- WebSocket: `ws://localhost:8000/xiaozhi/v1/`

## 📁 目录结构

```
xiaoyun-lite/
├── app.py                      # 主入口
├── config.yaml                 # 配置文件
├── requirements.txt
│
├── core/                       # 核心模块
│   ├── websocket_server.py     # WebSocket 服务器
│   ├── connection.py           # 连接处理
│   ├── providers/              # AI Provider (ASR/TTS/LLM完整)
│   ├── handle/                 # 消息处理
│   └── utils/                  # 工具函数
│
├── ai_engine/                  # AI 引擎
│   └── audio_analysis/         # 音频情感分析
│       ├── lite_inference.py   # MobileNet 推理
│       ├── spectrogram_extractor.py
│       └── audio_analyzer.py   # 统一接口
│
├── plugins/                    # 插件系统
│   └── functions/              # 插件函数
│
└── integration/                # 集成配置
    └── unified_service.py      # Server B 集成
```

## 🎯 核心功能

### 1. WebSocket 通信

```javascript
// UniApp 客户端连接
const ws = uni.connectSocket({
    url: 'ws://localhost:8000/xiaozhi/v1/',
    header: {
        'device-id': `uniapp_${userId}`,
        'client-id': clientId
    }
})

// 发送聊天消息
ws.send({
    type: 'chat',
    content: '你好',
    enable_audio: false
})
```

### 2. 音频情感分析

```python
from ai_engine.audio_analysis import AudioEmotionAnalyzer

# 初始化分析器 (可选 MobileNet 模型)
analyzer = AudioEmotionAnalyzer(
    model_path='ai_engine/audio_analysis/models/mobilenet_quantized/model_mobile.ptl'
)

# 分析音频
result = analyzer.analyze('audio.mp3')

print(result['interpretation']['mood'])  # "平静愉悦"
print(result['predictions']['valence'])  # 0.35
print(result['predictions']['arousal']) # -0.12
```

### 3. Server B 画像集成

```python
from integration import UnifiedServiceClient

client = UnifiedServiceClient('http://tag.zrj666.cn:8003')

# 获取用户画像
persona = client.get_user_persona(user_id)
# 返回: "用户画像:\n- 当前心情状态: 平静愉悦 (情绪价 0.35)..."

# 注入到对话上下文
system_prompt = f"{default_prompt}\n\n{persona}"
```

## ⚙️ 配置说明

### AI Provider 配置

```yaml
# 选择使用的 AI 服务
selected_module:
  LLM: "LLM_zhipu"          # 智谱AI (免费)
  TTS: "TTS_edgetts"        # Edge TTS (免费)
  ASR: "ASR_funasr"         # FunASR (本地)
  Memory: "Memory_local_short"

# 各服务商配置
LLM:
  LLM_zhipu:
    type: zhipu
    api_key: "你的智谱AI密钥"
    model: "glm-4-flash"
```

### 音频分析配置

```yaml
# 音频情感分析
audio_emotion:
  enabled: true
  model_path: "./ai_engine/audio_analysis/models/mobilenet_quantized/model_mobile.ptl"
  device: "cpu"
  fallback: "mfcc"  # MobileNet失败时使用MFCC
```

### Server B 集成

```yaml
integration:
  unified_service_url: "http://tag.zrj666.cn:8003"
  enable_persona: true
  persona_cache_ttl: 300
```

## 🧪 测试

### 测试 WebSocket

```bash
python test_websocket.py
```

### 测试音频分析

```bash
python test_audio_analysis.py
```

## 📊 性能指标

| 指标 | 数值 |
|------|------|
| 模型大小 | 3MB (MobileNet量化) |
| 内存占用 | <350MB |
| 音频分析速度 | 3-5秒/首 |
| 准确度 | 80-85% (MobileNet) |
| WebSocket 响应 | <50ms |

## 🔧 与原版对比

| 特性 | 原版 xiaozhi-server | xiaoyun-lite |
|------|---------------------|---------------------|
| WebSocket | ✅ | ✅ |
| AI Provider | ✅ | ✅ (完整保留) |
| ESP32 支持 | ✅ | ❌ (移除) |
| OTA 服务 | ✅ | ❌ (移除) |
| 音频情感分析 | ❌ | ✅ (新增) |
| 画像集成 | ❌ | ✅ (新增) |
| 代码量 | ~15000行 | ~10000行 |
| 依赖包 | 60+ | 40+ |

## 📝 开发文档

- [AI Provider 文档](docs/ai_providers.md)
- [插件开发指南](docs/plugin_development.md)
- [音频分析 API](docs/audio_analysis_api.md)
- [WebSocket 协议](docs/websocket_protocol.md)

## 🤝 集成架构

```
UniApp 前端 (manager-mobile)
    ↓ WebSocket
xiaoyun-lite
    ↓ HTTP
Server B (unified-music-service + llm-service)
    ↓ HTTP
Server A (audio-analysis-worker)
```

## ❓ 常见问题

**Q: MobileNet 模型在哪里下载?**

A: 模型文件应该在 `manager-mobile/ServerB/models/` 目录中,如果没有需要手动训练或从备份恢复。

**Q: 如何切换到纯 MFCC 分析?**

A: 在 `config.yaml` 中设置 `audio_emotion.model_path: null`

**Q: 支持哪些音频格式?**

A: 支持 mp3, wav, flac, opus 等所有 librosa 支持的格式。

## 📄 许可证

与原项目保持一致

## 🙏 致谢

- 原始 `xiaozhi-server` 项目
- MobileNet 音频分析算法
- Server B 统一音乐服务

---

**版本**: v1.0  
**创建时间**: 2026-01-19  
**维护者**: 项目团队
