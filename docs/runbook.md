# 运行与排障手册

更新时间：2026-07-03  
适用环境：当前服务器 `/home/zhong/SMART-MEETING2 (copy)(lx)/smart-meeting-ai`

## 1. 当前访问地址

| 模块 | 地址 | 说明 |
| --- | --- | --- |
| 会议记录 | `https://localhost:5179` | 通过前端代理访问会议主系统 |
| 老记 | `https://localhost:5181/laoji` | 独立老记页面 |
| 会议后端 | `http://127.0.0.1:8020` | 主后端服务 |
| 老记后端 | `http://127.0.0.1:8035` | 老记轻量服务 |
| Qwen ASR 服务 | `http://127.0.0.1:8030` | 短音频/ASR 辅助服务 |

远程 SSH 使用时，需要在 VS Code “端口”面板转发对应端口。浏览器地址栏访问的是本地 `localhost`，实际请求会转发到服务器。

## 2. 会议记录启动

启动前先确保日志目录存在：

```bash
mkdir -p logs
```

### 2.1 后端

```bash
cd "/home/zhong/SMART-MEETING2 (copy)(lx)/smart-meeting-ai/backend"
.venv/bin/python3.11 -m uvicorn app.main:app --host 0.0.0.0 --port 8020 --ws-ping-interval 25 --ws-ping-timeout 300
```

### 2.2 前端代理

```bash
cd "/home/zhong/SMART-MEETING2 (copy)(lx)/smart-meeting-ai/frontend"
../backend/.venv/bin/python3.11 serve_with_proxy.py --port 5179 --backend http://127.0.0.1:8020 --enable-https
```

访问：

```text
https://localhost:5179
```

## 3. 老记启动

### 3.1 轻量后端

```bash
cd "/home/zhong/SMART-MEETING2 (copy)(lx)/smart-meeting-ai/backend"
ENV=local CORS_ORIGINS='["*"]' .venv/bin/python3.11 -m uvicorn app.laoji.main:app --host 0.0.0.0 --port 8035
```

### 3.2 前端代理

```bash
cd "/home/zhong/SMART-MEETING2 (copy)(lx)/smart-meeting-ai/frontend"
../backend/.venv/bin/python3.11 serve_with_proxy.py --port 5181 --backend http://127.0.0.1:8035 --enable-https
```

访问：

```text
https://localhost:5181/laoji
```

## 4. 健康检查

服务器本机检查时建议带上 `NO_PROXY`，避免代理影响本地请求。

```bash
NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost curl -k -sS https://127.0.0.1:5181/api/health
```

期望返回：

```json
{"status":"ok","env":"local","service":"laoji"}
```

老记解析测试：

```bash
NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost curl -k -sS \
  -X POST https://127.0.0.1:5181/api/laoji/parse \
  -H 'Content-Type: application/json' \
  -d '{"text":"老记老记，下周三下午两点和周总开会，要讨论硬件相关问题。"}'
```

## 5. 日志位置

| 日志 | 说明 |
| --- | --- |
| `logs/frontend.log` | 会议主前端代理日志 |
| `logs/laoji_frontend.log` | 老记前端代理日志 |
| `logs/laoji_backend.log` | 老记轻量后端日志 |
| `logs/qwen_asr.log` | Qwen ASR 服务日志 |


`logs/` 和 `*.log` 已在 `.gitignore` 中忽略。

## 6. 常见问题

### 6.1 浏览器提示连接被拒绝

通常是对应端口的服务没有启动，或 VS Code 端口没有转发。

检查端口：

```bash
ss -tlnp
```

重点看 `5179`、`5181`、`8020`、`8035`。

### 6.2 HTTPS 证书不可信

前端代理使用本地自签证书。浏览器第一次访问 `https://localhost:5179` 或 `https://localhost:5181` 时需要在高级选项中继续访问。

麦克风、系统音频、屏幕共享等浏览器能力通常要求 HTTPS 或可信 localhost。

### 6.3 老记录音有波形但没有文字

说明浏览器已经采集到音频，但 ASR 可能没有返回结果。排查顺序：

1. 看页面状态是否停在“正在转写”。
2. 查 `logs/laoji_backend.log`。
3. 查 `logs/qwen_asr.log` 或 FunASR 服务日志。
4. 用文字输入直接点“生成”，确认解析链路是否正常。

### 6.4 会议总结很慢

会议总结慢通常不是前端问题，而是本地 LLM 推理、显存占用、上下文过长或任务串行导致。建议后续改成异步任务，并把 Qwen 32B 只用于高质量离线总结，日常调试优先用 8B 或更小模型。

### 6.5 localhost 在远程 SSH 下能不能用

可以。浏览器里的 `localhost:5179` 指的是本机，但 VS Code 端口转发会把它转到远程服务器的对应端口。前提是 VS Code “端口”面板里已经转发了该端口。
