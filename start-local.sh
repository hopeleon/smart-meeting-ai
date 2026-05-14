#!/usr/bin/env bash
set -e

# ── 颜色 ────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[✓]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
error() { echo -e "${RED}[✗]${NC} $1"; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ── 1. 检查依赖 ─────────────────────────────────
echo ""
echo "========================================="
echo "  智能会议记录平台 - 本地启动"
echo "========================================="
echo ""

# Node.js
if ! command -v node &>/dev/null; then
    error "未找到 node，请先安装 Node.js 18+ (https://nodejs.org)"
fi
NODE_VER=$(node -v | sed 's/v//' | cut -d. -f1)
if [ "$NODE_VER" -lt 18 ]; then
    error "Node.js 版本过低 ($(node -v))，需要 18+"
fi
info "Node.js $(node -v)"

# Python（Windows Git Bash 只有 python，Linux/macOS 通常用 python3）
PYTHON=""
for cmd in python python3; do
    if command -v "$cmd" &>/dev/null; then
        PYTHON="$cmd"
        break
    fi
done
[ -z "$PYTHON" ] && error "未找到 python，请先安装 Python 3.11+"
PY_VER=$($PYTHON -c "import sys; print(sys.version_info.minor)")
if [ "$PY_VER" -lt 11 ]; then
    error "Python 版本过低，需要 3.11+"
fi
info "$($PYTHON --version 2>&1) ($PYTHON)"

# Redis（可选）
if command -v redis-cli &>/dev/null && redis-cli ping &>/dev/null 2>&1; then
    info "Redis 已运行（Celery 将使用 Redis）"
    export REDIS_AVAILABLE=true
else
    warn "Redis 未安装或未运行，Celery 将使用 eager 模式（任务同步执行）"
    export REDIS_AVAILABLE=false
fi

# ── 2. 安装后端依赖 ──────────────────────────────
echo ""
info "安装后端 Python 依赖..."
cd "$SCRIPT_DIR/backend"

# 创建虚拟环境（如果不存在）
if [ ! -d ".venv" ]; then
    $PYTHON -m venv .venv
    info "已创建 Python 虚拟环境"
fi

# 激活虚拟环境（Windows 用 Scripts，Linux/macOS 用 bin）
if [ -f ".venv/Scripts/activate" ]; then
    source .venv/Scripts/activate
elif [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
else
    error "虚拟环境激活脚本不存在"
fi

pip install --upgrade pip 2>/dev/null || true
pip install --quiet "uvicorn[standard]"
pip install --quiet -r requirements.txt
info "后端依赖安装完成"

# ── 3. 安装前端依赖 ──────────────────────────────
echo ""
info "安装前端 npm 依赖..."
cd "$SCRIPT_DIR/frontend"
npm install --silent 2>/dev/null || npm install
info "前端依赖安装完成"

# ── 4. 启动服务 ──────────────────────────────────
echo ""
echo "========================================="
echo "  启动服务..."
echo "========================================="
echo ""
echo "  后端 API:  http://localhost:8000"
echo "  API 文档:  http://localhost:8000/docs"
echo "  前端界面:  http://localhost:5173"
echo ""
echo "  按 Ctrl+C 停止所有服务"
echo "========================================="
echo ""

# 清理函数
cleanup() {
    echo ""
    info "正在停止服务..."
    [ -n "$BACKEND_PID" ] && kill "$BACKEND_PID" 2>/dev/null
    [ -n "$FRONTEND_PID" ] && kill "$FRONTEND_PID" 2>/dev/null
    wait 2>/dev/null
    info "已停止所有服务"
}
trap cleanup EXIT INT TERM

# 启动后端
cd "$SCRIPT_DIR/backend"
export ENV=local
export CORS_ORIGINS='["http://localhost","http://localhost:5173","http://127.0.0.1:5173"]'
$PYTHON -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload &
BACKEND_PID=$!
info "后端已启动 (PID: $BACKEND_PID)"

# 等待后端启动
sleep 2

# 启动前端
cd "$SCRIPT_DIR/frontend"
VITE_MOCK_MODE=false VITE_API_BASE_URL=http://localhost:8000/api VITE_WS_BASE_URL=ws://localhost:8000/ws npx vite --host 0.0.0.0 --port 5173 &
FRONTEND_PID=$!
info "前端已启动 (PID: $FRONTEND_PID)"

# 等待任意子进程退出
wait -n 2>/dev/null || wait
