# 独立消融实验平台 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不使用 `exp/` 的前提下，构建独立、可复现的 D/V/S 三层消融实验平台，并产出可用于毕业论文的功能与性能证据。

**Architecture:** 所有新资产置于 `xiaoyun-server-lite/ablation_experiments/`。配置矩阵生成每组不可变快照；WebSocket 探针和 HTTP 采集器产生 JSONL 原始观测；GPT 匿名评审器输出结构化质量评分；汇总器生成指标、统计检验和 Markdown 表格。服务代码通过配置覆盖切换，不修改既有 `exp/` 或其结果。

**Tech Stack:** Python 3.10+、pytest、websockets、httpx、psutil、PyYAML、OpenAI Python SDK、CSV/JSONL。

## Global Constraints

- 所有新建文件仅位于 `xiaoyun-server-lite/ablation_experiments/`，设计和计划文档除外。
- 禁止读取、导入、复制或写入 `xiaoyun-server-lite/exp/` 及其结果。
- 每个实验组从完整系统基线叠加最小配置覆盖；完整快照和 SHA-256 指纹必须与每次运行绑定。
- 原始 JSONL 记录只追加，聚合结果写入 `results/`，二者不得混写。
- GPT 仅作为匿名评审器，必须隐藏组别与模块名称，并对每个 A/B 样本随机化、反向化、重复评审 3 次。
- 性能数据剔除预热样本，但超时与失败请求必须保留并计入成功率。
- 新增含中文文件使用 UTF-8；修改后执行 UTF-8 读取与 `git diff` 核对。

---

## File Structure

- `ablation_experiments/README.md`：独立实验的运行顺序、环境变量、目录约定和清理规则。
- `ablation_experiments/requirements.txt`：实验工具的最小依赖。
- `ablation_experiments/configs/groups.yaml`：D/V/S 组定义、基线开关与覆盖值。
- `ablation_experiments/configs/runtime.example.yaml`：环境端点和测试标识示例，不含密钥。
- `ablation_experiments/datasets/dialogue_cases.jsonl`：新的分层多轮对话样本。
- `ablation_experiments/datasets/voice_cases.jsonl`：新的短、中、长语音链路样本。
- `ablation_experiments/prompts/gpt_judge_v1.md`：匿名 A/B 评审提示词和 JSON 输出契约。
- `ablation_experiments/scripts/config_matrix.py`：解析组定义、生成配置叠加快照与指纹。
- `ablation_experiments/scripts/records.py`：JSONL 追加、运行元数据与分位数计算。
- `ablation_experiments/scripts/ws_probe.py`：真实 WebSocket 会话采集器。
- `ablation_experiments/scripts/service_probe.py`：Server B、Server A 和 MySQL 结果链路采集器。
- `ablation_experiments/scripts/gpt_judge.py`：GPT 匿名评审、重试和原始判定保存。
- `ablation_experiments/scripts/aggregate.py`：D/V/S 指标、胜率与配对统计汇总。
- `ablation_experiments/scripts/fault_proxy.py`：测试专用的延迟与超时注入反向代理。
- `ablation_experiments/scripts/run_experiment.py`：预检、分组执行、并发调度和目录归档入口。
- `ablation_experiments/tests/`：各脚本的单元和契约测试。

### Task 1: 创建独立实验骨架与配置矩阵

**Files:**
- Create: `ablation_experiments/requirements.txt`
- Create: `ablation_experiments/README.md`
- Create: `ablation_experiments/configs/groups.yaml`
- Create: `ablation_experiments/configs/runtime.example.yaml`
- Create: `ablation_experiments/scripts/config_matrix.py`
- Test: `ablation_experiments/tests/test_config_matrix.py`

**Interfaces:**
- Produces: `load_group_matrix(path: Path) -> dict[str, dict]`、`build_group_snapshot(matrix: dict, group_id: str) -> dict`、`snapshot_fingerprint(snapshot: dict) -> str`。
- Consumes: `groups.yaml` 中的 `baseline` 和 `groups.<id>.overrides`。

- [ ] **Step 1: 写入失败测试，锁定 D1/D2/D3/D4、V1/V2/V3、S1/S2/S3 的最小覆盖规则。**

```python
def test_d1_only_disables_dialogue_policy(tmp_path):
    matrix = load_group_matrix(write_matrix(tmp_path))
    snapshot = build_group_snapshot(matrix, "D1")
    assert snapshot["dialogue_policy"]["enabled"] is False
    assert snapshot["integration"]["enable_persona"] is True
    assert snapshot["sentence_pipeline"]["enabled"] is True
```

- [ ] **Step 2: 运行失败测试。**

Run: `pytest ablation_experiments/tests/test_config_matrix.py -v`

Expected: FAIL，原因是 `config_matrix` 尚未定义。

- [ ] **Step 3: 实现矩阵解析、递归叠加和稳定指纹。**

```python
def snapshot_fingerprint(snapshot: dict) -> str:
    canonical = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
```

`groups.yaml` 的基线必须显式包含 `dialogue_policy.enabled`、`enable_stable_memory`、`enable_music_hypothesis`、`integration.enable_persona`、`audio_emotion.enabled` 与 `sentence_pipeline.enabled`；每个消融组仅覆写其目标字段。

- [ ] **Step 4: 运行测试并检查配置快照。**

Run: `pytest ablation_experiments/tests/test_config_matrix.py -v`

Expected: PASS。

- [ ] **Step 5: 提交骨架与配置矩阵。**

```bash
git add ablation_experiments
git commit -m "feat: add isolated ablation configuration matrix"
```

### Task 2: 记录模型与统计基础设施

**Files:**
- Create: `ablation_experiments/scripts/records.py`
- Test: `ablation_experiments/tests/test_records.py`

**Interfaces:**
- Produces: `RunMetadata`、`append_jsonl(path: Path, record: dict) -> None`、`percentile(values: list[float], p: float) -> float`、`summarize_latencies(records: list[dict], field: str) -> dict[str, float]`。
- Consumes: 每条记录的 `run_id`、`group_id`、`config_sha256`、`case_id`、`status` 和 ISO 8601 时间戳。

- [ ] **Step 1: 写入失败测试，确保 JSONL 只追加且分位数保留失败计数。**

```python
def test_summary_reports_success_rate_and_p95(tmp_path):
    records = [{"status": "ok", "latency_ms": 10}, {"status": "timeout"}, {"status": "ok", "latency_ms": 30}]
    summary = summarize_latencies(records, "latency_ms")
    assert summary["request_count"] == 3
    assert summary["success_rate"] == pytest.approx(2 / 3)
    assert summary["p95_ms"] >= 10
```

- [ ] **Step 2: 运行失败测试。**

Run: `pytest ablation_experiments/tests/test_records.py -v`

Expected: FAIL，原因是记录模块不存在。

- [ ] **Step 3: 实现记录结构与统计函数。**

每条记录必须包含 `schema_version: "1"`、`run_id`、`group_id`、`config_sha256`、`case_id`、`started_at`、`finished_at`、`status`。失败记录的时延字段为 `null`，并由 `error_type` 区分 `timeout`、`connection_error`、`protocol_error`。

- [ ] **Step 4: 运行测试。**

Run: `pytest ablation_experiments/tests/test_records.py -v`

Expected: PASS。

- [ ] **Step 5: 提交记录基础设施。**

```bash
git add ablation_experiments/scripts/records.py ablation_experiments/tests/test_records.py
git commit -m "feat: add ablation run record utilities"
```

### Task 3: 构建新样本集与真实 WebSocket 探针

**Files:**
- Create: `ablation_experiments/datasets/dialogue_cases.jsonl`
- Create: `ablation_experiments/datasets/voice_cases.jsonl`
- Create: `ablation_experiments/scripts/ws_probe.py`
- Test: `ablation_experiments/tests/test_ws_probe.py`

**Interfaces:**
- Produces: `load_cases(path: Path) -> list[dict]`、`run_ws_case(case: dict, endpoint: str, timeout_s: float) -> dict`。
- Consumes: `case_id`、`category`、`turns`、`input_text`、`expected_signal`；WebSocket 事件流。

- [ ] **Step 1: 写入失败测试，验证事件时间线产生首包、首句语音与完整响应指标。**

```python
def test_extract_timeline_tracks_first_audio_and_completion():
    metrics = extract_timeline([
        {"kind": "sent", "at_ms": 0},
        {"kind": "text", "at_ms": 120},
        {"kind": "audio", "at_ms": 260},
        {"kind": "completed", "at_ms": 840},
    ])
    assert metrics["t_first_token_ms"] == 120
    assert metrics["t_first_audio_ms"] == 260
    assert metrics["t_complete_ms"] == 840
```

- [ ] **Step 2: 运行失败测试。**

Run: `pytest ablation_experiments/tests/test_ws_probe.py -v`

Expected: FAIL，原因是 `ws_probe` 尚未实现。

- [ ] **Step 3: 实现协议适配与时间线提取。**

探针只接受运行时 `WS_URL`，以每个 `case_id` 创建隔离设备标识；对文本、音频、结束和错误事件记录单调时钟。生成 36 条新的对话样本，按情绪表达、求助、安静陪伴、音乐信号、未完话题和边界情境各 6 条；生成 20 条新的短、中、长语音样本。

- [ ] **Step 4: 运行离线协议测试。**

Run: `pytest ablation_experiments/tests/test_ws_probe.py -v`

Expected: PASS。

- [ ] **Step 5: 提交样本与探针。**

```bash
git add ablation_experiments/datasets ablation_experiments/scripts/ws_probe.py ablation_experiments/tests/test_ws_probe.py
git commit -m "feat: add isolated websocket ablation probe"
```

### Task 4: 采集 Server A、Server B 与数据一致性指标

**Files:**
- Create: `ablation_experiments/scripts/service_probe.py`
- Test: `ablation_experiments/tests/test_service_probe.py`

**Interfaces:**
- Produces: `probe_persona(base_url: str, user_id: str) -> dict`、`poll_analysis(base_url: str, music_id: str, deadline_s: float) -> dict`、`verify_task_consistency(db_config: dict, task_id: str) -> dict`。
- Consumes: `SERVICE_B_URL`、`MYSQL_HOST`、`MYSQL_PORT`、`MYSQL_DATABASE`、`MYSQL_USER`、`MYSQL_PASSWORD`。

- [ ] **Step 1: 写入失败测试，覆盖成功、超时和服务返回非成功结构。**

```python
def test_poll_analysis_marks_timeout(monkeypatch):
    monkeypatch.setattr(service_probe, "fetch_analysis", lambda *_: {"state": "pending"})
    result = poll_analysis("http://service-b", "m1", deadline_s=0)
    assert result["status"] == "timeout"
    assert result["analysis_latency_ms"] is None
```

- [ ] **Step 2: 运行失败测试。**

Run: `pytest ablation_experiments/tests/test_service_probe.py -v`

Expected: FAIL，原因是服务探针不存在。

- [ ] **Step 3: 实现健康检查、画像计时、分析轮询和数据库一致性查询。**

服务探针必须记录 `persona_hit`、`cache_hint`、`task_enqueue_rate`、`task_success_rate`、`analysis_latency_ms` 与 `consistency_ok`。数据库凭据只从环境变量读取；原始记录中不得写入密码。

- [ ] **Step 4: 运行测试。**

Run: `pytest ablation_experiments/tests/test_service_probe.py -v`

Expected: PASS。

- [ ] **Step 5: 提交服务探针。**

```bash
git add ablation_experiments/scripts/service_probe.py ablation_experiments/tests/test_service_probe.py
git commit -m "feat: add service chain ablation probe"
```

### Task 5: 实现 GPT 匿名 A/B 评审器

**Files:**
- Create: `ablation_experiments/prompts/gpt_judge_v1.md`
- Create: `ablation_experiments/scripts/gpt_judge.py`
- Test: `ablation_experiments/tests/test_gpt_judge.py`

**Interfaces:**
- Produces: `build_judge_request(case: dict, reply_a: str, reply_b: str, order_seed: int) -> dict`、`validate_judgment(payload: dict) -> dict`、`judge_pair(...) -> list[dict]`。
- Consumes: `OPENAI_API_KEY`、`GPT_JUDGE_MODEL`、匿名回复对和输出 JSON Schema。

- [ ] **Step 1: 写入失败测试，验证匿名化、顺序反转与 JSON 契约。**

```python
def test_judge_request_hides_group_ids_and_reverses_order():
    request = build_judge_request({"case_id": "c1"}, "回复甲", "回复乙", order_seed=7)
    assert "D0" not in request["prompt"]
    assert set(request["labels"].values()) == {"A", "B"}
    assert request["reverse_prompt"] != request["prompt"]
```

- [ ] **Step 2: 运行失败测试。**

Run: `pytest ablation_experiments/tests/test_gpt_judge.py -v`

Expected: FAIL，原因是评审器尚未实现。

- [ ] **Step 3: 实现 GPT 调用与契约验证。**

评审器固定温度 `0`，每个回复对执行正序、反序与第三次独立随机序评审。输出 JSON 必须包含七个 1 至 5 分维度、`winner`、`reason` 与 `invalid_case`。调用失败最多重试 2 次；三次失败后写入 `judge_error` 原始记录，不伪造分数。

- [ ] **Step 4: 运行单元测试。**

Run: `pytest ablation_experiments/tests/test_gpt_judge.py -v`

Expected: PASS。

- [ ] **Step 5: 提交评审器。**

```bash
git add ablation_experiments/prompts ablation_experiments/scripts/gpt_judge.py ablation_experiments/tests/test_gpt_judge.py
git commit -m "feat: add anonymous gpt ablation judge"
```

### Task 6: 实现故障注入与 D/V/S 汇总器

**Files:**
- Create: `ablation_experiments/scripts/fault_proxy.py`
- Create: `ablation_experiments/scripts/aggregate.py`
- Test: `ablation_experiments/tests/test_fault_proxy.py`
- Test: `ablation_experiments/tests/test_aggregate.py`

**Interfaces:**
- Produces: `FaultProfile(delay_ms: int, timeout_rate: float)`、`aggregate_dialogue(records: list[dict]) -> dict`、`aggregate_voice(records: list[dict]) -> dict`、`aggregate_services(records: list[dict]) -> dict`。
- Consumes: `TEST_FAULT_DELAY_MS`、`TEST_FAULT_TIMEOUT_RATE`，以及 `raw/<run_id>/*.jsonl`。

- [ ] **Step 1: 写入失败测试，锁定故障范围与三类表格口径。**

```python
def test_fault_proxy_only_matches_test_header():
    assert should_inject_fault({"X-Ablation-Test": "run-1"}) is True
    assert should_inject_fault({}) is False

def test_aggregate_voice_reports_first_audio_and_completion():
    result = aggregate_voice([{"status": "ok", "t_first_audio_ms": 200, "t_complete_ms": 700}])
    assert result["t_first_audio_p50_ms"] == 200
    assert result["t_complete_p50_ms"] == 700
```

- [ ] **Step 2: 运行失败测试。**

Run: `pytest ablation_experiments/tests/test_fault_proxy.py ablation_experiments/tests/test_aggregate.py -v`

Expected: FAIL，原因是对应模块不存在。

- [ ] **Step 3: 实现仅对测试头生效的代理和聚合器。**

代理仅在 `X-Ablation-Test` 存在时注入延迟或超时，普通业务流量直通。汇总器输出 `results/<run_id>/table_d.csv`、`table_v.csv`、`table_s.csv`、`summary.json` 和 `report.md`；全部表含样本量、成功率、均值、标准差、P50、P95、P99 与相对 D0/V0/S0 变化。

- [ ] **Step 4: 运行测试。**

Run: `pytest ablation_experiments/tests/test_fault_proxy.py ablation_experiments/tests/test_aggregate.py -v`

Expected: PASS。

- [ ] **Step 5: 提交故障注入与汇总器。**

```bash
git add ablation_experiments/scripts/fault_proxy.py ablation_experiments/scripts/aggregate.py ablation_experiments/tests
git commit -m "feat: add ablation fault injection and aggregation"
```

### Task 7: 编排执行、预检与端到端验收

**Files:**
- Create: `ablation_experiments/scripts/run_experiment.py`
- Modify: `ablation_experiments/README.md`
- Test: `ablation_experiments/tests/test_run_experiment.py`

**Interfaces:**
- Produces: `preflight(runtime: dict) -> list[str]`、`run_group(group_id: str, runtime: dict, run_id: str) -> Path`。
- Consumes: `groups.yaml`、`runtime.yaml`、`WS_URL`、`SERVICE_B_URL`、GPT 环境变量和新建数据集。

- [ ] **Step 1: 写入失败测试，验证预检拒绝缺失端点且运行目录隔离。**

```python
def test_preflight_requires_websocket_and_service_b():
    errors = preflight({"ws_url": "", "service_b_url": ""})
    assert "WS_URL" in errors
    assert "SERVICE_B_URL" in errors

def test_run_directory_isolated(tmp_path):
    path = create_run_directory(tmp_path, "D0", "20260720T120000Z")
    assert path == tmp_path / "20260720T120000Z_D0"
```

- [ ] **Step 2: 运行失败测试。**

Run: `pytest ablation_experiments/tests/test_run_experiment.py -v`

Expected: FAIL，原因是编排入口尚未实现。

- [ ] **Step 3: 实现 CLI 编排。**

提供 `preflight`、`run --group D0`、`run --series D`、`judge --run-id <id>`、`aggregate --run-id <id>` 子命令。运行前将当前配置快照复制至 `raw/<run_id>/config_snapshot.yaml`；运行后保存机器信息、Python 版本、依赖版本与命令行参数。README 给出从预检到汇总的完整命令。

- [ ] **Step 4: 运行全量测试和一次 D0 冒烟。**

Run: `pytest ablation_experiments/tests -v`

Expected: PASS。

Run: `python ablation_experiments/scripts/run_experiment.py preflight --runtime ablation_experiments/configs/runtime.yaml`

Expected: 所有已配置服务显示 `ok`；缺失凭据仅阻止对应 GPT 阶段。

- [ ] **Step 5: 执行 UTF-8、差异和状态检查后提交。**

```bash
git diff --check
git status --short
git add ablation_experiments
git commit -m "feat: add isolated ablation experiment runner"
```

## Self-Review

- 规格覆盖：D 系列由任务 1、3、5、6、7 覆盖；V 系列由任务 1、3、6、7 覆盖；S 系列由任务 1、4、6、7 覆盖；GPT 协议由任务 5 覆盖；独立目录与可追溯性由任务 1、2、7 覆盖。
- 范围检查：计划只建设实验平台及其测试资产，不修改业务逻辑、不接触 `exp/`、不迁移数据。
- 一致性：所有原始记录共享 `run_id`、`group_id`、`config_sha256` 和 `case_id`；汇总器只读取 `raw/<run_id>`。
