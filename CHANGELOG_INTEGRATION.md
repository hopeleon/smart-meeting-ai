# smart-meeting-ai × InsightEye 模式二 功能整合文档

> 本文档记录将 InsightEye 模式二（实时说话人识别）功能整合到 smart-meeting-ai 的全部修改内容，
> 逐项对比与原版 InsightEye 的思路差异。

---

## 一、已完成的全部修改

### 1.1 新增文件

| 文件路径 | 来源 | 说明 |
|---------|------|------|
| `backend/app/asr/__init__.py` | 新建 | ASR 模块包初始化 |
| `backend/app/asr/config.py` | 新建 | InsightEye config shim |
| `backend/app/asr/model_manager.py` | 移植 | FunASR + CAM++ + Silero VAD 加载器（含多窗口融合），支持 ./models/ 相对路径解析 |
| `backend/app/asr/vad_asr_pipeline.py` | **完全移植** | 完整 StreamingPipeline（原始代码逐行对齐） |
| `backend/app/asr/enhanced_engine.py` | 新写 | 实时说话人识别引擎 |
| `backend/app/asr/realtime_session.py` | 新写 | 实时会话状态存储 |
| `backend/app/asr/realtime_ws_handler.py` | 新写 | FastAPI WebSocket 处理器 |
| `backend/app/model_manager.py` | 新建 | shim → app.asr.model_manager |
| `backend/app/speaker_database.py` | 新建 | shim → services.speaker_db_service |
| `backend/app/services/speaker_db_service.py` | 移植 | 声纹数据库 CRUD（250 speakers） |
| `backend/app/api/speakers.py` | 新写 | 声纹管理 REST API |
| `backend/app/schemas/speaker.py` | 新写 | Pydantic Schema |
| `frontend/src/api/speakers.ts` | 新写 | 前端声纹 API + Mock 数据 |
| `frontend/src/pages/SpeakerDbPage.tsx` | 新写 | 声纹管理页面（录音注册+列表+相似度分析） |
| `backend/data/speaker_voiceprints.db` | 迁移 | InsightEye 声纹数据库（250 speakers） |

### 1.2 修改的文件

| 文件路径 | 修改内容 |
|---------|---------|
| `backend/app/api/router.py` | 追加 `speakers.router` 路由 |
| `backend/app/main.py` | lifespan 中同步阻塞加载 ASR 模型，/health 接口返回各模型就绪状态 |
| `backend/app/config.py` | 新增 5 个模型路径字段，修复 APP_ENV/ENV 冲突 |
| `backend/app/asr/config.py` | 修复 fallback 路径（移除 iic/ 前缀），消除循环导入 |
| `backend/app/asr/model_manager.py` | initialize() 新增 _load_vad() 调用，调整 VAD 路径优先级（./models 优先），新增 _resolve_model_path() |
| `backend/app/asr/__init__.py` | 移除不存在的 TranscriptionResult，补充 IdentificationResult/MultiSpeakerResult 导出 |
| `backend/requirements.txt` | 补充 torch、torchaudio、funasr 依赖 |
| `backend/requirements-prod.txt` | 同上 |
| `.env.example` | 新增模型路径配置（Docker 容器内 /app/models/） |
| `docker-compose.yml` | backend/celery-worker 挂载 ./models:/app/models:ro |
| `docker-compose.prod.yml` | backend/celery-worker 使用命名卷 models_data，添加 nvidia GPU 支持 |
| `backend/Dockerfile` | 基础镜像改为 nvidia/cuda:12.1.0，pip install PyTorch CUDA 12.1，支持 GPU 推理 |
| `frontend/src/App.tsx` | 新增 `/speakers` 路由 |
| `frontend/src/components/layout/Navbar.tsx` | 新增声纹管理导航 |
| `frontend/src/api/websocket.ts` | 新增 `sendAudioChunk()` 和 `endMeeting()` |
| `frontend/src/components/audio/RecordingControls.tsx` | 重写为 MediaRecorder 实时录音，支持麦克风/系统音频切换 |
| `frontend/src/pages/MeetingActivePage.tsx` | 集成 WebSocket 接收 transcript_delta |
| `frontend/src/types/meeting.ts` | 新增 `transcript_delta` 类型 |
| `frontend/src/stores/audioStore.ts` | 新增 `audioSource` 状态（mic/system） |

---

## 二、实现思路逐项对比

### 2.1 核心架构

| 功能模块 | InsightEye 模式二 | smart-meeting-ai | 状态 |
|---------|------------------|------------------|------|
| **麦克风 + 系统音频切换** | 麦克风 (`getUserMedia`) + 系统音频 (`getDisplayMedia`) | ✅ 麦克风 (`getUserMedia`) + 系统音频 (`getDisplayMedia`) | 完全一致 |
| **VAD 检测** | Silero VAD（torch.jit）+ 能量过滤 | ✅ Silero VAD + 能量过滤 | 完全一致 |
| **VAD 参数** | 512 samples, threshold=0.30, energy=0.005 | ✅ 完全一致 | 完全一致 |
| **ASR 模型** | FunASR Paraformer-large | ✅ FunASR Paraformer-large | 完全一致 |
| **声纹模型** | CAM++（192维） | ✅ CAM++（192维） | 完全一致 |
| **说话人变化检测** | CAM++ embedding + 滑动窗口 + 跳变检测 + BinarySeg | ✅ 完全一致 | 完全一致 |
| **多窗口投票融合** | 3-5 窗口，mean 融合 | ✅ 完全一致 | 完全一致 |
| **说话人合并策略** | 缓冲区等待 + 超时提交 + uncertain 处理 | ✅ 完全一致 | 完全一致 |
| **声纹 uncertain** | top1/top2 差距 < 0.03 + 音频 < 2000ms | ✅ 完全一致 | 完全一致 |
| **标点恢复** | ct-punc 模型 | ✅ ct-punc（通过 FunASR） | 完全一致 |
| **MacBERT 纠错** | 支持（可关闭） | 不使用 | ✅ 符合需求 |
| **RNNoise 去噪** | 支持（可关闭） | 不使用 | ✅ 符合需求 |

---

## 三、关键参数逐项对比

### 3.1 VAD 配置

| 参数 | InsightEye | smart-meeting-ai | 一致 |
|------|-----------|-----------------|------|
| `VAD_SAMPLE_RATE` | 16000 | 16000 | ✅ |
| `VAD_WINDOW_SIZE` | 512 | 512 | ✅ |
| `VAD_THRESHOLD` | 0.30 | 0.30 | ✅ |
| `MIN_SPEECH_DURATION_MS` | 600 | 600 | ✅ |
| `MIN_SILENCE_DURATION_MS` | 500 | 500 | ✅ |
| `MIN_SPEECH_ENERGY_THRESHOLD` | 0.005 | 0.005 | ✅ |
| `SEGMENT_OVERLAP_TAIL_MS` | 200 | 200 | ✅ |

### 3.2 说话人变化检测配置

| 参数 | InsightEye | smart-meeting-ai | 一致 |
|------|-----------|-----------------|------|
| `embedding_interval_ms` | 100.0 | 100.0 | ✅ |
| `embedding_min_for_detection` | 5 | 5 | ✅ |
| `change_distance_threshold` | 0.10 | 0.10 | ✅ |
| `change_confirm_count` | 1 | 1 | ✅ |
| `min_change_interval_ms` | 800.0 | 800.0 | ✅ |
| `use_dynamic_threshold` | True | True | ✅ |
| `dynamic_factor` | 0.3 | 0.3 | ✅ |
| `use_sliding_window` | True | True | ✅ |
| `sliding_window_size` | 4 | 4 | ✅ |
| `detect_jump_only` | True | True | ✅ |
| `jump_threshold` | 0.08 | 0.08 | ✅ |
| `jump_confirm_count` | 1 | 1 | ✅ |
| `min_segment_duration_ms` | 2000.0 | 2000.0 | ✅ |
| `warmup_duration_ms` | 1500.0 | 1500.0 | ✅ |
| `offline_changepoint` | True | True | ✅ |
| `cp_min_segment_ms` | 800.0 | 800.0 | ✅ |
| `cp_penalty` | 0.2 | 0.2 | ✅ |

### 3.3 声纹识别参数

| 参数 | InsightEye | smart-meeting-ai | 一致 |
|------|-----------|-----------------|------|
| `SPEAKER_SIMILARITY_THRESHOLD` | 0.5 | 0.5 | ✅ |
| `MAX_SPEAKERS` | 10 | 10 | ✅ |
| `SPEAKER_STRICT_GAP_THRESHOLD` | 0.03 | 0.03 | ✅ |
| `SPEAKER_SHORT_AUDIO_THRESHOLD_MS` | 2000 | 2000 | ✅ |
| `SPEAKER_TOP_GAP_THRESHOLD` | 0.03 | 0.03 | ✅ |
| 零向量过滤 | 1e-6 | 1e-6 | ✅ |
| cosine similarity | `(cos + 1) / 2` | `(cos + 1) / 2` | ✅ |
| CAM++ 特征维度 | 192 维 | 192 维 | ✅ |
| embedding 归一化 | L2 norm | L2 norm | ✅ |
| `_SPEAKER_EMB_SIM_THRESHOLD` | 0.65 | 0.65 | ✅ |

### 3.4 多窗口投票参数

| 参数 | InsightEye | smart-meeting-ai | 一致 |
|------|-----------|-----------------|------|
| 默认窗口数 `n_windows` | 3 | 3 | ✅ |
| `window_step_ratio` | 0.25 | 0.25 | ✅ |
| `vote_method` | score_weighted | score_weighted | ✅ |
| 融合方法 | mean/median | mean/median | ✅ |
| 最小窗口音频 | 0.3 秒 | 0.3 秒 | ✅ |
| 零能量跳过 | 1e-4 | 1e-4 | ✅ |
| 离群点阈值 | 2.5 std | 2.5 std | ✅ |

### 3.5 合并策略参数

| 参数 | InsightEye | smart-meeting-ai | 一致 |
|------|-----------|-----------------|------|
| `_merge_enabled` | True | True | ✅ |
| `_merge_max_wait_ms` | 10000.0 | 10000.0 | ✅ |
| `_merge_min_same_label` | 2 | 2 | ✅ |
| `_merge_min_duration_ms` | 500.0 | 500.0 | ✅ |

### 3.6 模型加载配置

| 项目 | InsightEye 原始 | smart-meeting-ai | 一致/改进 |
|------|----------------|-----------------|---------|
| FunASR `ncpu` | 4 | 4 | ✅ |
| FunASR `disable_update` | True | True | ✅ |
| FunASR `punc_model_revision` | v2.0.4 | v2.0.4 | ✅ |
| 标点模型 `ncpu` | 2 | 2 | ✅ |
| CAM++ 中文 `embedding_size` | 192 | 192 | ✅ |
| CAM++ 英文 `embedding_size` | 512 | 512 | ✅ |
| Silero VAD `torch.set_num_threads` | 1 | 1 | ✅ |
| FunASR 加载方式 | 单一路经 | 双重 fallback（本地路径优先） | ✅ 改进 |
| Silero VAD 加载优先级 | ModelScope → 下载 | **D:\InsightEye\models > ModelScope > 下载** | ✅ 改进 |
| `INSIGHTEYE_MODELS` 环境变量 | 无 | 新增，优先查 D:\InsightEye\models | ✅ 改进 |

> **模型路径改进说明**：原始 InsightEye 仅从 `LOCAL_MODEL_DIR` 搜索 Silero VAD。smart-meeting-ai 新增了 `INSIGHTEYE_MODELS` 环境变量支持，优先在 `D:\InsightEye\models` 目录查找已缓存的模型，避免重复下载。

### 3.7 去噪 / MacBERT 配置

| 参数 | InsightEye 原始 | smart-meeting-ai | 一致 |
|------|----------------|-----------------|------|
| `DENOISE_ENABLED` | True | False | ✅（按需开启） |
| `DENOISE_BACKEND` | rnnoise | rnnoise | ✅ |
| `ENABLE_MACBERT_CORRECTION` | False | False | ✅ |

---

## 四、方法级逐项对比

| 方法 | InsightEye | smart-meeting-ai | 状态 |
|------|-----------|-----------------|------|
| `StreamingVAD.feed()` | ✅ | ✅ | 一致 |
| `StreamingVAD._detect_speech()` | ✅ | ✅ | 一致 |
| `StreamingVAD.extract_pending_embeddings()` | ✅ | ✅ | 一致 |
| `StreamingVAD._find_changepoint_offline()` | ✅ | ✅ | 一致 |
| `StreamingVAD._detect_speaker_change_by_embedding()` | ✅ | ✅ | 一致 |
| `StreamingVAD._update_state()` | ✅ | ✅ | 一致 |
| `StreamingASR.recognize()` | ✅ | ✅ | 一致 |
| `StreamingSpeakerRecognition.extract_and_compare()` | ✅ | ✅ | 一致 |
| `StreamingSpeakerRecognition.extract_multi_window()` | ✅ | ✅ | 一致 |
| `StreamingSpeakerRecognition.extract_fused()` | ✅ | ✅ | 一致 |
| `StreamingPipeline.register_speaker()` | ✅ | ✅ | 一致 |
| `StreamingPipeline._labels_match()` | ✅ | ✅ | 一致 |
| `StreamingPipeline._check_speaker_uncertainty()` | ✅ | ✅ | 一致 |
| `StreamingPipeline._check_speaker_similarity_for_merge()` | ✅ | ✅ | 一致 |
| `StreamingPipeline._should_commit_segments()` | ✅ | ✅ | 一致 |
| `StreamingPipeline._merge_segments()` | ✅ | ✅ | 一致 |
| `StreamingPipeline._add_segment_to_buffer()` | ✅ | ✅ | 一致（含 uncertain 处理） |
| `StreamingPipeline._recognize_speaker_label()` | ✅ | ✅ | 一致（含 uncertainty 检查） |
| `StreamingPipeline._embedding_extraction_loop()` | ✅ | ✅ | 一致 |
| `StreamingPipeline._run_asr_locked()` | ✅ | ✅ | 一致 |
| `StreamingPipeline.feed_audio()` | ✅ | ✅ | 一致 |
| `StreamingPipeline.stop()` | ✅ | ✅ | 一致 |
| `StreamingPipeline.print_segment_stats()` | ✅ | ✅ | 一致 |
| `SpeakerEmbeddingExtractor.extract_multi_window()` | ✅ | ✅ | 一致 |
| `SpeakerEmbeddingExtractor.extract_fused()` | ✅ | ✅ | 一致 |
| `SpeakerEmbeddingExtractor._detect_speech_ranges()` | ✅ | ✅ | 一致 |
| `SpeakerEmbeddingExtractor.compute_similarity()` | ✅ | ✅ | 一致 |

### 4.1 model_manager.py 新增 / 补充方法（本次检查修复）

| 方法 | 说明 | 来源 |
|------|------|------|
| `_load_punc_model()` | 独立加载标点恢复模型（ct-punc，ncpu=2） | 从 InsightEye 补充 |
| `_load_campplus_en()` | 加载 CAM++ 英文声纹模型（VoxCeleb，embedding_size=512） | 从 InsightEye 补充 |
| `get_punc_model()` | 返回标点恢复模型实例 | 新增 getter |
| `get_camp_en_model()` | 返回 CAM++ 英文模型实例 | 新增 getter |
| `_load_funasr()` 双重 fallback | 本地路径失败后 fallback 到 model_id + cache_dir | 从 InsightEye 补充 |
| 初始化状态汇总打印 | 启动时显示 VAD/ASR/CAM++/CAM++_EN 四个模型加载状态 | 从 InsightEye 补充 |

---

## 五、运行方式

### 5.1 环境准备

1. 复制 `.env.example` 为 `.env`，配置模型路径：
```bash
FUNASR_MODEL_DIR=D:/model/iic/speech_paraformer-large-vad-punc_asr_nat-zh-cn-16k-common-vocab8404-pytorch
CAMPPLUS_MODEL_DIR=D:/model/iic/speech_campplus_sv_zh-cn_16k-common
LOCAL_MODEL_DIR=D:/model
INSIGHTEYE_MODELS=D:/InsightEye/models
LOCAL_DEVICE=cuda
```

2. 安装依赖：
```bash
cd backend
pip install -r requirements.txt
```

### 5.2 启动后端

```bash
cd backend
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 5.3 启动前端

```bash
cd frontend
npm run dev
```

### 5.4 功能入口

- 声纹管理：`http://localhost:5173/speakers`
- 实时会议：`http://localhost:5173/meeting/{meeting_id}`

---

## 六、文件变更总览

```
smart-meeting-ai/
├── backend/
│   ├── app/
│   │   ├── asr/                    ← ASR 模块（完全对齐 InsightEye）
│   │   │   ├── __init__.py
│   │   │   ├── config.py
│   │   │   ├── model_manager.py    # 含 extract_multi_window/fused
│   │   │   ├── vad_asr_pipeline.py # 完整 StreamingPipeline（原始代码）
│   │   │   ├── enhanced_engine.py
│   │   │   ├── realtime_session.py
│   │   │   └── realtime_ws_handler.py
│   │   ├── model_manager.py        # shim
│   │   ├── speaker_database.py     # shim
│   │   ├── services/
│   │   │   └── speaker_db_service.py  # 250 speakers
│   │   ├── api/speakers.py
│   │   ├── schemas/speaker.py
│   │   ├── main.py
│   │   └── config.py
│   ├── requirements.txt
│   └── data/
│       └── speaker_voiceprints.db   # 250 speakers
├── frontend/
│   └── src/
│       ├── api/speakers.ts          # 前端 API + Mock
│       ├── pages/SpeakerDbPage.tsx  # 声纹管理页面
│       ├── components/audio/RecordingControls.tsx
│       ├── components/layout/Navbar.tsx
│       ├── App.tsx
│       ├── api/websocket.ts
│       └── types/meeting.ts
└── .env.example
```

---

## 七、与 InsightEye 的差异总结

**当前实现与 InsightEye 模式二完全对齐**，主要差异如下：

| 差异项 | 说明 | 是否可选 |
|--------|------|---------|
| MacBERT 纠错 | 未实现（用户明确不需要） | 不可选 |
| RNNoise 去噪 | 未实现（可按需开启 `DENOISE_ENABLED = True`） | 可选 |
| 模型加载优先级 | 额外优先搜索 `D:\InsightEye\models` | 改进，无负面影响 |
| FunASR 加载 fallback | 新增 model_id + cache_dir 备选方案 | 改进，无负面影响 |
| CAM++ 英文模型 | 补充英文声纹识别支持 | 增强，无负面影响 |

这四个改进均不影响原功能，在模型文件不存在时自动降级到原始行为。

---

*文档生成时间：2026-05-14*
