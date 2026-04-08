# Dialogue Policy Plan（个性化陪伴聊天）

## 1. 目标与原则

### 1.1 目标
- 把当前“直接喂给 LLM 的对话”升级为“策略层 + 受控生成”。
- 提升聊天自然度：有节奏、有边界、有记忆。
- 把用户画像/音乐信号从“结论”改为“假设”，通过对话确认。

### 1.2 原则
- 先共情，再推进；先确认，再建议。
- 一轮只做一件主要事情（一个主意图 + 一个微技能）。
- 记忆精注入：只注入最相关 1~3 条，避免信息噪声。
- 固定安全边界：不诊断、不越权、不做过度承诺。

---

## 2. 总体架构（在 LLM 前增加策略层）

```text
User Input + Recent Context + Music/Emotion Signals + Memory Candidates
                           |
                           v
                  Dialogue Policy Layer
          (intent + style + memory selection + micro-skill)
                           |
                           v
                  Prompt Assembler (受控拼接)
                           |
                           v
                         LLM
                           |
                           v
                  Response + Memory Writer
```

---

## 3. 策略层（Dialogue Policy）

### 3.1 输入
- 用户本轮文本。
- 最近 4~8 轮对话。
- 音乐行为信号（若可用）。
- 候选记忆（stable + episodic）。
- 风险信号（低落/高风险关键词等）。

### 3.2 输出（结构化）
- `intent`: `reflect|clarify|guide|advise|shift|summarize_followup`
- `tone`: `gentle|neutral|lively`
- `length`: `short|medium|long`
- `question_density`: `low|high`
- `initiative`: `low|high`
- `suggestion_strength`: `0|1|2`
- `music_role`: `opening|empathy_medium|action|none`
- `micro_skill_id`: 本轮选择的微技能
- `memory_to_inject`: 1~3 条（含来源类型与摘要）
- `emotion_hypothesis`: `{label, confidence, source}`
- `need_confirmation`: `true|false`
- `risk_level`: `normal|low_mood|high_risk`
- `boundary_flags`: 触发的边界规则列表

### 3.3 节奏规则
- 推荐主路径：`Reflect -> Clarify -> Guide/Advise -> Summarize&Follow-up`
- 用户明显抗拒推进时：降低主动性、降低建议强度。
- 连续两轮避免都用建议类风格。

---

## 4. 微技能库（Micro-skills）

### 4.1 设计要求
- 每轮最多启用 1 个微技能。
- 由策略层选择，不由 LLM自由发挥。
- 模板填空后作为本轮约束，提升稳定性。

### 4.2 首批高命中技能
1. `reflect_label`
- 模板：`听起来你有点{emotion_phrase}。`
- 用途：共情开场。

2. `careful_attribution`
- 模板：`我感觉你可能更像在{state}，我理解得对吗？`
- 用途：软确认，不下定论。

3. `choice_question`
- 模板：`你更想{option_a}，还是想{option_b}？`
- 用途：降低沟通成本，给用户控制感。

4. `permission_advice`
- 模板：`我可以给你一个很短的办法，你想听吗？`
- 用途：许可式建议，避免强推。

5. `soft_wrapup`
- 模板：`今天先到这也可以，我明天再跟你确认一下{follow_topic}。`
- 用途：收口与跟进。

---

## 5. 两层记忆设计

## 5.1 Stable Memory（稳定偏好）
- 内容：称呼偏好、回复长短偏好、建议偏好（鸡汤/方法）、禁忌、长期音乐偏好。
- 更新：仅在用户明确表达时更新（低频）。
- 注入：每轮最多 1~2 条。

### 5.2 Episodic Memory（事件记忆）
- 内容：最近 3~10 条事件卡片（事件、情绪线索、未完话题、时间）。
- 更新：每轮结束提炼 0~1 条。
- 注入：每轮最多 1~3 条（按相关性 + 新近性）。

### 5.3 注入预算
- 总注入上限：1~3 条。
- 优先级：未完话题 > 本轮主题相关 > 新近事件。

---

## 6. 音乐信号使用策略

### 6.1 定位
- 音乐信号是弱证据，不是诊断依据。
- 画像与音乐仅用于提出假设，必须可被用户纠正。

### 6.2 三种使用模式（每轮选一）
- `opening`：用音乐做自然开场。
- `empathy_medium`：用音乐作为共情媒介。
- `action`：提出一个轻行动（推荐歌/2分钟呼吸/晚安电台）。

### 6.3 软确认机制
- 示例：`你刚听的这首偏低唤醒，是想放空一下吗？`
- 用户确认后再推进；若否认，立即调整假设。

---

## 7. 固定边界策略（硬规则）

- 不做心理诊断，不贴人格标签。
- 不替代医疗/心理/法律等专业帮助。
- 用户明显低落时：先陪伴，再鼓励联系现实支持资源。
- 语气温柔但不过度承诺。
- 涉及高风险话题时，强制切换到 `support_first` 模式。

---

## 8. 分阶段落地计划（当前项目）

### Phase 0：设计对齐（本阶段）
- 产出策略输出 Schema。
- 定义微技能模板与触发规则。
- 定义 stable/episodic 记忆结构。

### Phase 1：最小可用版本（MVP）
- 在 `core/connection.py` 的 `chat()` 前插入 Policy 调用点。
- 先做规则版 Policy（无需新模型）：
  - 产出 intent/style/memory_to_inject/micro_skill。
- 改造 prompt 组装，仅注入 1~3 条记忆。

### Phase 2：记忆分层
- 复用现有短期记忆能力作为 episodic 基底。
- 新增 stable memory 存储与检索。
- 增加“未完话题”标记与跟进能力。

### Phase 3：策略增强
- 加入软确认逻辑与置信度门控。
- 完成音乐信号与对话校验闭环。
- 增加评估看板（自然度/打断率/建议接受率）。

---

## 9. 评估指标

- 回复自然度（人工标注/AB 测试）。
- 用户追问率（越低越好，说明表达清晰）。
- 建议接受率（许可式建议后用户同意比例）。
- 负反馈率（“别分析我”“别说教”等）。
- 对话连续性（未完话题在后续被正确跟进的比例）。

---

## 10. 风险与缓解

- 风险：策略层过复杂导致响应变慢。
- 缓解：先规则版实现，字段固定，避免重推理。

- 风险：记忆误召回造成“尴尬个性化”。
- 缓解：注入预算上限 + 软确认机制。

- 风险：音乐信号误判。
- 缓解：只作假设，不作结论；用户否认立即修正。

---

## 11. 下一步执行清单

1. 先冻结 `Policy Output Schema v1`（JSON 字段定稿）。
2. 定义 5 个微技能的触发表与模板槽位。
3. 设计 stable/episodic 存储结构与读取优先级。
4. 在 `chat()` 前接入规则版策略层（MVP）。
5. 进行 20~50 段对话回放评估并调整阈值。

