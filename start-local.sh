#!/usr/bin/env bash
#
# 智能会议记录平台 - 本地启动脚本
# 用法: ./start-local.sh
#
# 端口约定（集中定义在本文件顶部，所有引用均从变量派生）：
#   前端 = ${FRONTEND_PORT} (默认 5179)
#   后端 = ${BACKEND_PORT} (默认 8020)
# 项目路径含空格（SMART-MEETING2 (copy)），所有路径类变量必须加双引号。
#

set -u  # 未定义变量报错
set -o pipefail
trap '' PIPE  # 忽略 SIGPIPE（Cursor IDE 会 head -30）

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/backend"
FRONTEND_DIR="$SCRIPT_DIR/frontend"

# ── 修复 PATH（venv prepends its bin, cursor node path from shell config is lost）──
CURSOR_NODE="/home/zhong/.cursor-server/bin/linux-x64/806df57ed3b6f1ee0175140d38039a38574ec720"
if [[ ":$PATH:" != *":$(dirname "$CURSOR_NODE"):"* ]] && [ -x "$CURSOR_NODE/node" ]; then
    export PATH="$CURSOR_NODE:$PATH"
fi

# ── 端口配置（集中管理，改这里即可）────────────────
BACKEND_PORT=8020
FRONTEND_PORT=5179
BACKEND_URL="http://127.0.0.1:${BACKEND_PORT}"
FRONTEND_URL="http://127.0.0.1:${FRONTEND_PORT}"

# ── 颜色 ────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info()  { echo -e "${GREEN}[✓]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
error() { echo -e "${RED}[✗]${NC} $1"; exit 1; }

# ── 日志文件 ────────────────────────────────────
mkdir -p "$SCRIPT_DIR/logs"
BACKEND_LOG="$SCRIPT_DIR/logs/backend.log"
FRONTEND_LOG="$SCRIPT_DIR/logs/frontend.log"
SESSION_LOG="$SCRIPT_DIR/log.txt"
> "$SESSION_LOG"  # 清空 session 日志

# ── Python 检测（优先 venv，其次系统 python3.11）────────────────
# 所有路径可能含空格，取值时一律加引号。
find_python() {
    # 1) venv
    if [ -x "$BACKEND_DIR/.venv/bin/python3.11" ]; then
        printf '%s\n' "$BACKEND_DIR/.venv/bin/python3.11"
        return
    fi
    # 2) 系统 python3.11
    if [ -x /usr/bin/python3.11 ]; then
        printf '%s\n' /usr/bin/python3.11
        return
    fi
    # 3) PATH 中的 python3
    if command -v python3 >/dev/null 2>&1; then
        printf '%s\n' "$(command -v python3)"
        return
    fi
    return 1
}

PYTHON="$(find_python)" || error "未找到 python3，请先安装 Python 3.11+"

PY_VERSION="$("$PYTHON" --version 2>&1)"
PY_MAJOR="$("$PYTHON" -c "import sys; print(sys.version_info.minor)")"
if [ "$PY_MAJOR" -lt 11 ]; then
    error "Python 版本过低（$PY_VERSION），需要 3.11+"
fi

# ── 清理旧进程 ─────────────────────────────────────
cleanup_stale() {
    echo -e "${YELLOW}[清理]${NC} 停止旧进程..."
    pkill -f "uvicorn.*app.main" 2>/dev/null && echo "  已停止 uvicorn" || true
    pkill -f "qwen_asr_service/server.py" 2>/dev/null && echo "  已停止 Qwen-ASR 微服务" || true
    pkill -f "tail -f.*backend.log" 2>/dev/null && echo "  已停止 tail 日志进程" || true
    pkill -f "serve_with_proxy" 2>/dev/null && echo "  已停止前端服务" || true
    pkill -f "ollama serve" 2>/dev/null && echo "  已停止 Ollama" || true
    # 清理僵尸进程
    for pid in $(ps aux 2>/dev/null | grep -E "\[python3.11\]\s+<defunct>" | awk '{print $2}'); do
        kill -9 "$pid" 2>/dev/null && echo "  已清理僵尸进程 $pid" || true
    done
    sleep 1
}
cleanup_stale

# ── 清理 GPU 显存 ─────────────────────────────────
cleanup_gpu() {
    echo -e "${YELLOW}[GPU]${NC} 清理 CUDA 缓存..."
    "$PYTHON" - <<'EOF' 2>/dev/null
import gc
try:
    import torch
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.synchronize()
        print(f"  GPU 显存已清理，当前空闲: {torch.cuda.memory_allocated()//1024**2}MB / {torch.cuda.get_device_properties(0).total_memory//1024**3}GB")
except Exception as e:
    print(f"  跳过 GPU 清理: {e}")
gc.collect()
EOF
}
cleanup_gpu

# ── 启动 Ollama ───────────────────────────────────
start_ollama() {
    if ! pgrep -f "ollama serve" > /dev/null 2>&1; then
        echo -e "${BLUE}[Ollama]${NC} 启动中..."
        # CUDA_LAUNCH_BLOCKING=1 强制 CUDA kernel 同步执行，避免 Blackwell(RTX5090)
        # 等新架构 GPU 的 watchdog timer 导致推理超时
        nohup env CUDA_LAUNCH_BLOCKING=1 ollama serve > "$SCRIPT_DIR/logs/ollama.log" 2>&1 &
        OLLAMA_PID=$!
        echo -e "  Ollama 已启动 (PID: $OLLAMA_PID)"
    else
        echo -e "${GREEN}[Ollama]${NC} 已运行，跳过启动"
    fi
}
start_ollama

# ── 构建前端 ────────────────────────────────────────
build_frontend() {
    echo -e "${BLUE}[前端]${NC} 构建中..."
    cd "$FRONTEND_DIR"
    if [ ! -f "node_modules/.bin/vite" ]; then
        warn "Vite 未安装，跳过前端构建"
    else
        # 直接调用 vite cli（node 加载 ESM 模块，避免 shebang 路径问题）
        if npm run build >/dev/null 2>&1; then
            info "前端构建完成"
        else
            npm run build 2>&1 | tail -5
            warn "前端构建失败，使用现有 dist"
        fi
    fi
    cd "$SCRIPT_DIR"
}

# ── 启动 ────────────────────────────────────────
echo ""
echo "========================================="
echo "  智能会议记录平台 - 本地启动"
echo "  前端: ${FRONTEND_URL}  |  后端: ${BACKEND_URL}"
echo "========================================="
echo ""
info "Python $PY_VERSION"
echo ""

build_frontend

# 后端日志文件
mkdir -p "$SCRIPT_DIR/logs"
BACKEND_LOG="$SCRIPT_DIR/logs/backend.log"
FRONTEND_LOG="$SCRIPT_DIR/logs/frontend.log"

# 清空旧日志
> "$BACKEND_LOG"
> "$FRONTEND_LOG"

# ── Qwen3-ASR 近实时微服务（独立隔离环境 py3.12/cu130，不污染主 .venv）──
QWEN_VENV="/home/zhong/qwen3asr-venv"
QWEN_LOG="$SCRIPT_DIR/logs/qwen_asr.log"
if [ -x "$QWEN_VENV/bin/python" ] && [ -f "$BACKEND_DIR/qwen_asr_service/server.py" ]; then
    > "$QWEN_LOG"
    echo -e "${BLUE}[Qwen-ASR]${NC} 启动近实时转写微服务 (端口=8030)..."
    setsid nohup "$QWEN_VENV/bin/python" "$BACKEND_DIR/qwen_asr_service/server.py" 8030 >> "$QWEN_LOG" 2>&1 < /dev/null &
    echo -e "  Qwen-ASR PID=$!，日志: $QWEN_LOG（首次加载模型约 30s）"
else
    warn "Qwen-ASR 隔离环境或脚本缺失，跳过（Qwen 模式不可用，不影响其它模式）"
fi

echo -e "${BLUE}[后端]${NC} 启动中 (端口=${BACKEND_PORT})..."
cd "$BACKEND_DIR"
export ENV=local
export HF_HUB_OFFLINE=1  # HF 模型已缓存，离线加载避免 mirror HEAD 超时卡死(预热/pyannote)+加速启动
export HF_ENDPOINT="https://hf-mirror.com"  # huggingface.co 不可达，模型走国内镜像/本地缓存，避免启动卡超时
export CORS_ORIGINS='["http://localhost","http://localhost:5179","http://127.0.0.1:5179","https://localhost:5179","https://127.0.0.1:5179","https://183.36.243.124:5179"]'
export PYTHONPATH="$SCRIPT_DIR:$BACKEND_DIR:$SCRIPT_DIR/meetingsummary"
# 使用 nohup + 绝对路径 PYTHON，所有带空格的路径加引号
nohup "$PYTHON" -m uvicorn app.main:app --host 0.0.0.0 --port "${BACKEND_PORT}" --ws-ping-interval 25 --ws-ping-timeout 300 >> "$BACKEND_LOG" 2>&1 &
BACKEND_PID=$!
echo -e "  uvicorn PID=$BACKEND_PID，日志: $BACKEND_LOG"

# 等待后端就绪（同时检查模型预热完成状态）
MODEL_READY=0
PORT_LISTENING=0
echo ""
echo -e "${YELLOW}────────────────────────────────────────────────────────────${NC}"
echo -e "${YELLOW}  实时转录模型加载进度（实时更新中）${NC}"
echo -e "${YELLOW}────────────────────────────────────────────────────────────${NC}"
for i in $(seq 1 120); do
    # 每 2 秒打印一行等待进度（同时写入 log.txt）
    _elapsed=$((i * 2))
    _dots=$(printf '%*s' $((i % 20)) '' | tr ' ' '.')
    echo -e "  ⏳ 正在等待后端服务启动... (${_elapsed}s) ${_dots}"
    echo "[$(date '+%H:%M:%S')] 等待后端服务启动... (${_elapsed}s)" >> "$SESSION_LOG"

    if ss -tlnp 2>/dev/null | grep -q ":${BACKEND_PORT}"; then
        if [ "$PORT_LISTENING" -eq 0 ]; then
            echo -e "  ${GREEN}✓${NC} 后端进程已启动，端口 ${BACKEND_PORT} 开始监听"
            echo "[$(date '+%H:%M:%S')] 后端端口 ${BACKEND_PORT} 已监听，正在加载模型..." >> "$SESSION_LOG"
            PORT_LISTENING=1
        fi
        # 端口已监听，立即打印 backend.log 中的最新模型加载日志
        _log_tail=$(tail -n 30 "$BACKEND_LOG" 2>/dev/null || true)
        if [ -n "$_log_tail" ]; then
            echo "$_log_tail" >> "$SESSION_LOG"
            echo -e "${GREEN}  → 模型加载日志:${NC}"
            echo "$_log_tail" | sed 's/^/     /'
        fi

        # 检查 models_ready
        if BACKEND_HEALTH="${BACKEND_URL}/api/health" "$PYTHON" - <<'PYEOF' 2>/dev/null
import urllib.request, json, os, sys
try:
    with urllib.request.urlopen(os.environ["BACKEND_HEALTH"], timeout=5) as r:
        d = json.loads(r.read().decode())
        if d.get('models_ready'):
            sys.exit(0)
except Exception:
    pass
sys.exit(1)
PYEOF
        then
            MODEL_READY=1
            break
        fi
    fi
    sleep 2
done

echo ""
if [ "$MODEL_READY" -eq 1 ]; then
    echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
    echo -e "  ${GREEN}✓${NC} 后端已就绪 (PID: $BACKEND_PID, ${BACKEND_URL})"
    echo -e "  ${GREEN}✓${NC} 实时转录模型全部加载完成"
    echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
    echo "[$(date '+%H:%M:%S')] 后端 + 模型加载就绪" >> "$SESSION_LOG"
else
    echo -e "${YELLOW}────────────────────────────────────────────────────────────${NC}"
    echo -e "  ${YELLOW}⚠${NC} 后端已启动 (PID: $BACKEND_PID, 端口=${BACKEND_PORT})"
    echo -e "  ${YELLOW}⚠${NC} 模型可能仍在后台加载，请查看下方日志了解进度"
    echo -e "${YELLOW}────────────────────────────────────────────────────────────${NC}"
    echo -e "  ${YELLOW}后端日志（最近 60 行）：${NC}"
    tail -n 60 "$BACKEND_LOG" 2>/dev/null | sed 's/^/  /' || true
    echo ""
    echo "[$(date '+%H:%M:%S')] 模型加载超时，请检查 logs/backend.log" >> "$SESSION_LOG"
fi

echo -e "${BLUE}[前端]${NC} 启动中 (端口=${FRONTEND_PORT}, HTTPS=开启)..."
cd "$FRONTEND_DIR"
"$PYTHON" serve_with_proxy.py --port "${FRONTEND_PORT}" --backend "${BACKEND_URL}" --enable-https >> "$FRONTEND_LOG" 2>&1 &
FRONTEND_PID=$!

# 等待前端就绪
for i in $(seq 1 15); do
    if ss -tlnp 2>/dev/null | grep -q ":${FRONTEND_PORT}"; then
        break
    fi
    sleep 1
done

if ss -tlnp 2>/dev/null | grep -q ":${FRONTEND_PORT}"; then
    info "前端已就绪 (PID: $FRONTEND_PID, ${FRONTEND_URL})"
else
    warn "前端启动超时，请检查 logs/frontend.log"
    echo -e "${YELLOW}[前端日志（最近 60 行）]${NC}"
    tail -n 60 "$FRONTEND_LOG" 2>/dev/null || true
fi

echo ""
echo "========================================="
echo "  智能会议记录平台"
echo "========================================="
echo ""
echo "  前端界面: ${FRONTEND_URL}"
echo "  后端 API:  ${BACKEND_URL}/api"
echo "  WebSocket: ${BACKEND_URL}/ws"
echo ""
echo "  后端日志：logs/backend.log"
echo "  前端日志：logs/frontend.log"
echo "  会话日志：log.txt（含实时模型加载进度）"
echo ""
echo "  按 Ctrl+C 停止所有服务"
echo "========================================="
echo ""

# ── 同时显示两个日志，同时写入 log.txt ────────────────────
echo -e "${YELLOW}[提示]${NC} 同时显示后端和前端日志（Ctrl+C 停止）"
echo ""

# ── 清理 ────────────────────────────────────────
cleanup() {
    echo ""
    echo -e "${RED}停止服务...${NC}"
    pkill -f "uvicorn.*app.main" 2>/dev/null && echo "  已停止 uvicorn" || true
    pkill -f "qwen_asr_service/server.py" 2>/dev/null && echo "  已停止 Qwen-ASR 微服务" || true
    pkill -f "tail -f.*backend.log" 2>/dev/null && echo "  已停止 tail 日志进程" || true
    pkill -f "serve_with_proxy" 2>/dev/null && echo "  已停止前端服务" || true
    # 保留 Ollama，不杀
    echo -e "${GREEN}已停止${NC}"
}
trap cleanup EXIT INT TERM

# 同时 tail 两个日志文件，输出到终端 + 追加到 log.txt
(tail -f "$BACKEND_LOG" & tail -f "$FRONTEND_LOG") | tee -a "$SESSION_LOG" > /dev/null &

# 等待任意 tail 退出（通常是 Ctrl+C）
wait
