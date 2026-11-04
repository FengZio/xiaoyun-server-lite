# UniApp 句级语音协议适配文档

后端已将 UniApp 回复链路升级为句级流水线。前端需要从“只处理整段 `reply/reply_audio`”改为“优先处理句级事件，保留聚合 `reply` 兼容”。

## 目标

前端需要支持：

- 同一轮回复由多个事件组成
- 同一 `message_id` 下存在多个句子
- 文本和音频分开到达
- 音频可能乱生成，但后端保证按 `sentence_index` 顺序下发
- 沉浸模式下可能没有 `reply_text_chunk`，但仍会有 `reply_text_final`

## 发送接口

WebSocket 发送 `chat`：

```json
{
  "type": "chat",
  "content": "你今天过得怎么样",
  "enable_audio": true,
  "session_id": "9f1278d8-8b58-4953-a201-d3590bf4143b",
  "agent_id": "725ca4815bdf46a6937c11eb003f3d0d",
  "entry_context": {
    "immersive_mode": false,
    "entry_source": "nfc",
    "entry_scene": "nfc"
  }
}
```

中断仍然是：

```json
{
  "type": "abort"
}
```

## 新增接收事件

### 1. `reply_start`

一轮回复开始。前端应初始化一个新的回复容器。

```json
{
  "type": "reply_start",
  "message_id": "bf96aac26e9547a4b7cb7473e4dbb59d",
  "session_id": "9f1278d8-8b58-4953-a201-d3590bf4143b",
  "mode": "normal",
  "reply_meta": {}
}
```

前端处理：

- 以 `message_id` 创建 reply record
- 初始化 `textChunks = []`、`audioChunks = []`
- 初始化播放队列

### 2. `reply_text_chunk`

一句文本到达。普通模式默认会收到，沉浸模式默认可能不发。

```json
{
  "type": "reply_text_chunk",
  "message_id": "bf96aac26e9547a4b7cb7473e4dbb59d",
  "session_id": "9f1278d8-8b58-4953-a201-d3590bf4143b",
  "sentence_index": 0,
  "text": "哈喽，我今天还不错呀。",
  "is_final_sentence": false,
  "reply_meta": {}
}
```

前端处理：

- 按 `message_id + sentence_index` 存储
- 追加到当前消息的展示文本
- 不要假设一次只来一条
- 不要用到达顺序代替 `sentence_index`

### 3. `reply_audio_chunk`

一句音频到达。每句一个音频 URL。

```json
{
  "type": "reply_audio_chunk",
  "message_id": "bf96aac26e9547a4b7cb7473e4dbb59d",
  "session_id": "9f1278d8-8b58-4953-a201-d3590bf4143b",
  "sentence_index": 0,
  "audio_url": "http://182.254.228.131:8013/audio/9f1278d8.../bf96.../000_1744450000000.mp3",
  "audio_format": "mp3",
  "reply_meta": {}
}
```

前端处理：

- 按 `sentence_index` 顺序入播放队列
- 可以边收边播
- 以 `message_id` 维度隔离不同轮回复
- `abort` 或切换上下文后，旧 `message_id` 音频不要继续播

### 4. `reply_text_final`

整轮聚合文本。用于最终落库、复制、分享、重连恢复。

```json
{
  "type": "reply_text_final",
  "message_id": "bf96aac26e9547a4b7cb7473e4dbb59d",
  "session_id": "9f1278d8-8b58-4953-a201-d3590bf4143b",
  "text": "哈喽，我今天还不错呀。早上差点睡过头，不过最后赶上了。",
  "sentence_count": 2,
  "reply_meta": {}
}
```

前端处理：

- 把它作为该轮回复的最终完整文本
- 如果 chunk 展示文本和 final 文本存在细微差异，以 `reply_text_final.text` 为准
- 聊天记录持久化建议使用这个字段

### 5. `reply_end`

整轮结束信号。

```json
{
  "type": "reply_end",
  "message_id": "bf96aac26e9547a4b7cb7473e4dbb59d",
  "session_id": "9f1278d8-8b58-4953-a201-d3590bf4143b",
  "status": "completed",
  "sentence_count": 2,
  "audio_chunk_count": 2,
  "reply_meta": {}
}
```

`status` 可能值：

- `completed`
- `partial`
- `aborted`

前端处理：

- 标记当前回复结束
- 若 `status=aborted`，收口 UI，停止等待后续 chunk
- 若 `status=partial`，说明有句子音频失败，但文本可能仍完整

## 兼容旧协议

后端仍会补发一个聚合版 `reply`：

```json
{
  "type": "reply",
  "message_id": "bf96aac26e9547a4b7cb7473e4dbb59d",
  "session_id": "9f1278d8-8b58-4953-a201-d3590bf4143b",
  "text": "哈喽，我今天还不错呀。早上差点睡过头，不过最后赶上了。",
  "state": "complete",
  "reply_meta": {}
}
```

前端建议：

- 新版本优先消费句级事件
- `reply` 仅作兜底兼容
- 如果同一 `message_id` 已处理过 `reply_text_final`，不要再重复渲染 `reply`

旧的整段 `reply_audio` 不再作为主协议依赖。

## 推荐前端数据结构

```ts
type ReplySession = {
  messageId: string
  sessionId: string
  mode: 'normal' | 'immersive'
  textChunks: Map<number, string>
  audioChunks: Map<number, AudioChunk>
  finalText?: string
  sentenceCount?: number
  status?: 'completed' | 'partial' | 'aborted'
  nextAudioPlayIndex: number
}
```

建议维护：

- `replyMap: Map<messageId, ReplySession>`
- 当前播放中的 `activeMessageId`
- 已取消或已过期的 `abortedMessageIds`

## 前端处理规则

### 1. 文本展示

- 普通模式：实时拼接 `reply_text_chunk`
- 沉浸模式：可不实时展示 chunk，但最终展示 `reply_text_final`
- 最终入库文本用 `reply_text_final.text`

### 2. 音频播放

- 按 `sentence_index` 顺序播放
- 即使音频晚到，也不能跳序播放后一句
- 某句缺失时等待后端后续句，直到 `reply_end`

### 3. 中断

- 用户发送 `abort` 后，立即停止当前 `message_id` 的播放
- 丢弃该 `message_id` 后续到达的 `reply_audio_chunk`
- 如收到 `reply_end.status=aborted`，更新 UI 为已中断

### 4. 消息合并

- 一轮回复以 `message_id` 为唯一键
- 不要按 socket 到达顺序直接渲染成多条聊天消息
- 同一 `message_id` 应归并为一条消息气泡

## 适配重点

必须完成：

- 支持 `reply_start`
- 支持 `reply_text_chunk`
- 支持 `reply_audio_chunk`
- 支持 `reply_text_final`
- 支持 `reply_end`
- 以 `message_id + sentence_index` 管理状态
- 音频顺序播放
- `abort` 后停止旧消息播放

建议完成：

- `reply` 兼容兜底
- `reply_meta` 驱动 UI 状态
- `partial` 状态展示“部分语音失败”

## 测试清单

前端至少验证这几项：

1. 普通模式  
   能实时显示多句文本，音频分句播放，最终正确合并。

2. 沉浸模式  
   即使没有 `reply_text_chunk`，也能播音频并在结束后显示完整文本。

3. 多句乱序生成场景  
   播放顺序仍按 `sentence_index`，不抢播后句。

4. 中途 `abort`  
   旧音频立即停止，后续旧 chunk 不再污染 UI。

5. `reply_text_final`  
   最终文本与 chunk 拼接结果一致，并作为聊天记录保存。
