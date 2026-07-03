# 企业实时语音识别三模式评估报告

评估日期：2026-07-02  
项目目录：`/home/zhong/SMART-MEETING2 (copy)(lx)/smart-meeting-ai`  
评估对象：FunASR、Whisper、Qwen 三种实时 WebSocket 模式  

## 1. 结论摘要

当前系统已经具备企业实时 ASR 对接的基本协议形态：WebSocket 长连接、二进制 PCM 推流、逐句 JSON 返回、会话 ID 隔离、三种识别模式入口。但从本轮环境启动和仓库既有评测结果看，距离企业稳定交付还差一层“工程化收敛”：

- **协议方向正确**：`/ws/meeting/{id}/funasr`、`/whisper`、`/qwen` 三种端点已与企业实时推流方式对齐。
- **当前本地环境不可直接完成三模式在线验收**：本轮启动中 FunASR ASR 模型加载失败，Qwen-ASR 微服务因无 CUDA 失败，健康检查无法稳定访问 `8020/8030`。
- **历史客观评测显示**：在 6 场合成中文会议上，Whisper 文本 CER 最低，Qwen 综合更稳，FunASR 说话人过分割最明显。
- **企业推荐路线**：优先把 **Qwen 模式** 打磨成默认推荐模式；保留 Whisper 作为高准确率/离线增强方案；FunASR 作为低延迟和中文抗噪备选，但需要先修复模型加载和过分割。

## 2. 本轮实测环境结果

### 2.1 启动方式

执行：

```bash
cd "/home/zhong/SMART-MEETING2 (copy)(lx)/smart-meeting-ai"
bash start-local.sh
```

脚本尝试启动：

- 后端：`http://127.0.0.1:8020`
- 前端：`http://127.0.0.1:5179`
- Qwen-ASR 微服务：`http://127.0.0.1:8030`
- Ollama：本地服务

### 2.2 健康检查

本轮健康检查：

| 服务 | 检查地址 | 结果 |
| --- | --- | --- |
| Backend | `http://127.0.0.1:8020/api/health` | 连接失败 |
| Qwen-ASR | `http://127.0.0.1:8030/health` | 连接失败 |

备注：后端日志出现过 `Uvicorn running on http://0.0.0.0:8020`，但在当前工具沙箱下外部命令无法稳定访问该端口，`ps` 也未能看到 uvicorn 进程。因此本轮没有把三路在线转写结果标为“成功实测”。

### 2.3 模型加载状态

后端日志摘要：

| 模型/组件 | 状态 | 说明 |
| --- | --- | --- |
| Silero VAD | PASS | 语音活动检测可加载 |
| FunASR ASR | FAIL | `FunASR ASR 加载失败（模型为空）` |
| 标点恢复 ct-punc | SKIP/FAIL | 尝试从 ModelScope 下载，受代理/网络限制失败 |
| CAM++ 中文声纹 | PASS | 先尝试 CUDA 失败，再退回 CPU |
| CAM++ 英文声纹 | PASS | 可选，先尝试 CUDA 失败，再退回 CPU |
| WhisperLiveKit | 启动后台预加载 | 日志显示开始预加载，但未完成可用性验证 |
| Qwen-ASR 微服务 | FAIL | `RuntimeError: No CUDA GPUs are available` |

本轮实测结论：

- 当前环境不满足 Qwen3-ASR 微服务的 CUDA 要求。
- FunASR 本地依赖/模型注册状态不完整，ASR 主模型没有成功加载。
- 端口检测脚本依赖 `ss`，在当前沙箱下无法判断监听状态，会造成启动脚本持续等待。

## 3. 仓库既有客观评测结果

仓库中已有评测材料：

- `test_meetings/评测报告.md`
- `test_meetings/评测结果.json`
- `test_meetings/transcripts/*`

测试数据说明：

- 语料来源：Primewords Chinese Corpus Set 1。
- 合成方式：6 场约 20 分钟中文“会议”，每场 5 位说话人，句间 0.3 秒静音。
- 标注：每场包含 `audio.wav`、`answer.json`、`answer.txt`。
- 指标：文本 CER（字错率，越低越好）和检测说话人数。

### 3.1 平均结果

| 模式 | 平均 CER ↓ | 检测说话人数 | 结论 |
| --- | ---: | ---: | --- |
| Whisper | **27.2%** | 未统一统计 | 文本准确率最好，但该结果来自 faster-whisper 批量转写，偏上界 |
| Qwen | 30.3% | 7.0 | 综合表现稳定，建议作为企业实时默认推荐 |
| FunASR | 32.1% | 9.7 | 文本略弱，且说话人过分割明显 |
| Whisper + pyannote | 文本同 Whisper | **5.3** | 说话人数量最接近真值 5 |

### 3.2 逐场结果

| 会议 | CER·Qwen | CER·FunASR | CER·Whisper | 说话人·Qwen | 说话人·FunASR | 说话人·pyannote |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| meeting_1 | 0.297 | 0.324 | **0.239** | 5 | 8 | 7 |
| meeting_2 | 0.350 | 0.370 | **0.326** | 6 | 10 | 5 |
| meeting_3 | **0.244** | 0.256 | 0.278 | 9 | 12 | 4 |
| meeting_4 | 0.338 | 0.356 | **0.248** | 8 | 7 | 6 |
| meeting_5 | 0.297 | 0.312 | **0.276** | 7 | 15 | 6 |
| meeting_6 | 0.291 | 0.309 | **0.263** | 7 | 6 | 4 |
| 平均 | 0.303 | 0.321 | **0.272** | 7.0 | 9.7 | 5.3 |

### 3.3 模式解读

**Qwen**

- 优点：中文实时综合表现最均衡，文本 CER 比 FunASR 低，说话人聚类比 FunASR 少过分割。
- 短板：强依赖独立 Qwen-ASR 微服务和 CUDA；当前本轮环境没有 GPU，因此不可用。
- 企业定位：默认推荐模式，适合作为主链路。

**Whisper**

- 优点：历史评测中文本 CER 最低；配合 pyannote 时，说话人数量最接近真值。
- 短板：历史 CER 是批量 faster-whisper 上界，不等同于实时流式表现；真实会议中可能出现繁体、方言标签、静音幻觉，需要后处理。
- 企业定位：质量优先或离线增强模式，也可作为主链路 fallback。

**FunASR**

- 优点：中文专用、低延迟潜力好，适合部署在资源较轻场景。
- 短板：本轮模型未成功加载；历史评测显示说话人过分割最明显。
- 企业定位：低延迟备选，需要先修复模型加载和说话人聚类策略。

## 4. 与企业实时对接标准的差距

### 4.1 已满足或基本满足

| 企业要求 | 当前状态 |
| --- | --- |
| 实时 WebSocket 推流 | 已有三种端点 |
| 会话隔离 | 使用 `{meeting_id}` |
| 二进制 PCM 推流 | 企业文档已明确 |
| 逐句 JSON 返回 | `transcript.completed` 已统一 |
| 说话人字段 | 已有 `speaker_id`、`speaker_name`、`identified`、`best_guess_name` |
| Python SDK | `sdk/asr_client.py` 已提供 |
| 文档 | `docs/实时语音识别接口-企业对接说明.md` 已存在 |

### 4.2 需要补齐

| 差距 | 影响 | 建议优先级 |
| --- | --- | --- |
| 模型启动不稳定 | 企业现场无法验收 | P0 |
| 缺少统一健康检查和 readiness | 无法判断三模式是否可用 | P0 |
| Qwen 强依赖 CUDA，未提供 CPU/降级策略 | 无 GPU 时模式直接不可用 | P0 |
| FunASR 依赖/模型注册失败 | FunASR 模式不可用 | P0 |
| 缺少 WSS + Token 鉴权 | 企业公网/专线接入风险高 | P0 |
| 错误码和错误事件不统一 | 企业侧难以自动重连/告警 | P1 |
| 缺少并发压测数据 | 无法承诺容量 | P1 |
| 缺少端到端延迟指标 | 无法量化“实时” | P1 |
| 说话人 DER 未测 | 分离质量无法严肃承诺 | P1 |
| 日志、音频、转写数据混放 | 运维和合规风险 | P1 |
| 大量 `.bak` 和运行产物混入源码 | 维护成本高 | P2 |

## 5. 优化路线建议

### 5.1 P0：先让企业验收链路稳定可跑

1. 修复启动脚本端口检测
   - 当前脚本依赖 `ss`，在受限环境会误判。
   - 建议改为 HTTP 轮询 `/api/health`，WebSocket 轮询三模式轻量握手。

2. 增加统一 readiness 接口
   - 建议新增：
     - `GET /api/asr/modes`
     - `GET /api/asr/health`
   - 返回每种模式：`available`、`ready`、`reason`、`device`、`model`、`last_error`。

3. 修复 Qwen 启动失败
   - 启动前检查 CUDA 和显存。
   - 无 GPU 时明确返回 `qwen unavailable: no cuda`，不要让企业连接后才失败。
   - 如企业现场 GPU 不稳定，提供 Qwen 关闭开关和 Whisper/FunASR fallback。

4. 修复 FunASR 模型加载
   - 检查 `backend/app/asr/model_manager.py` 中模型路径和 FunASR 版本兼容。
   - 将模型加载改成本地路径强约束，禁止生产启动时自动联网下载。
   - 把 optional 模型（标点）与 required 模型（ASR）区分清楚。

5. 企业鉴权和 WSS
   - 加 `Authorization: Bearer <token>` 或 `?token=` 握手校验。
   - Nginx 配置 WSS。
   - 增加会话级限流和最大连接时长。

### 5.2 P1：建立可交付质量指标

1. 三模式统一测试脚本
   - 输入同一批 `audio.wav`。
   - 输出每句文本、首包延迟、最终延迟、断连次数、错误事件。
   - 自动生成 Markdown/HTML 报告。

2. 增加真实会议测试集
   - 当前 Primewords 是干净朗读，不等于真实会议。
   - 需要覆盖：噪声、混响、多人抢话、远场麦克风、电话窄带、口音。

3. 增加 DER/身份识别评估
   - 当前只统计“检测说话人数”，不够严谨。
   - 企业最关心“谁在什么时候说了什么”，应补 DER、身份识别准确率、unknown 率。

4. 增加并发压测
   - 至少测：1/5/10/20 路并发。
   - 输出 GPU 显存、CPU、延迟、丢包、断连率。

5. 统一错误协议

建议错误事件：

```json
{
  "type": "error",
  "code": "MODEL_UNAVAILABLE",
  "message": "Qwen-ASR requires CUDA but no GPU is available",
  "retryable": false
}
```

### 5.3 P2：整理项目结构

建议目标：

```text
smart-meeting-ai/
├── backend/
├── frontend/
├── meetingsummary/
├── smart_meeting_app/
├── docs/
├── deploy/
├── sdk/
├── data/       # 测试集/语料/样本，不进 git
├── runtime/    # 日志、音频、总结、local.db，不进 git
└── archive/    # old-version 和 bak 文件
```

可直接清理的生成物：

- `__pycache__/`
- `.pytest_cache/`
- `frontend/dist/`
- `frontend/node_modules/`
- `smart_meeting_app/.dart_tool/`

建议归档而非直接删除：

- `old-version/`
- `*.bak*`
- `start-local.sh.bak_*`
- 历史日志 `logs/meeting_*.log`
- 历史输出 `backend/summaries/final/*`

## 6. 企业交付建议

### 默认推荐

| 场景 | 推荐模式 |
| --- | --- |
| 企业实时会议默认 | Qwen |
| 追求最高文本准确率 | Whisper |
| 轻量低延迟中文场景 | FunASR |
| 说话人分离优先 | Whisper + pyannote 或 Qwen + 调优后 CAM++ |

### 上线前最低验收门槛

建议在企业交付前至少满足：

- 三模式 `/api/asr/health` 均能明确返回 ready/unavailable。
- Qwen 默认链路可连续转写 60 分钟无崩溃。
- 单路实时端到端最终句延迟 P95 小于 5 秒。
- 5 路并发连续 30 分钟无断连。
- 明确 WSS 和 Token 鉴权方案。
- 发生模型不可用时，客户端收到结构化错误，而不是连接静默失败。
- 文档提供完整字段、错误码、音频格式、重连策略和示例代码。

## 7. 本轮整理动作

本轮新增报告：

- `docs/reports/enterprise-realtime-asr-evaluation-2026-07-02.md`
- `docs/reports/enterprise-realtime-asr-evaluation-2026-07-02.html`

本轮没有删除大型数据和历史输出。原因是当前目录是运行现场快照，大文件中包含测试集、模型、历史评测和日志，直接删除会影响复现。建议下一步先按 `codex.md` 和本报告中的目录方案做归档迁移，再删除确认无用的产物。

## 8. 下一步建议

优先顺序：

1. 修复启动和健康检查，让本地环境可稳定访问 `8020/8030`。
2. 修复 Qwen CUDA 检测和 FunASR 模型加载。
3. 写三模式统一自动化测试脚本，生成机器可复现报告。
4. 补 WSS、Token、错误码、并发压测。
5. 做目录归档：`data/`、`runtime/`、`archive/`。
