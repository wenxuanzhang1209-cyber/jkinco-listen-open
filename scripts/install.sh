#!/usr/bin/env bash
# 筑听开源本地版一键安装（macOS / Linux，非 Docker 路径）
#
# 用法：
#   bash scripts/install.sh
#   OLLAMA_MODEL=qwen2.5:14b bash scripts/install.sh   # 换更大模型
set -euo pipefail

PY="${PYTHON:-python3}"
OLLAMA_MODEL="${OLLAMA_MODEL:-qwen2.5:7b-instruct}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "==> 1/4 准备本地大模型（Ollama: ${OLLAMA_MODEL}）"
if ! command -v ollama >/dev/null 2>&1; then
  if [[ "$(uname)" == "Darwin" ]]; then
    brew install ollama
  else
    curl -fsSL https://ollama.com/install.sh | sh
  fi
fi
ollama pull "$OLLAMA_MODEL"

echo "==> 2/4 创建 Python 虚拟环境并安装依赖"
if [[ ! -d .venv ]]; then
  "$PY" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --upgrade pip >/dev/null
pip install -r requirements.txt

echo "==> 3/4 生成 .env（已存在则跳过）"
if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "已生成 .env，默认账号 admin / 123456"
fi

echo "==> 4/4 构建前端"
if ! command -v npm >/dev/null 2>&1; then
  echo "未找到 npm，请先安装 Node.js 22+"
  exit 1
fi
(
  cd frontend
  npm ci
  npm run build
)

echo
echo "✅ 安装完成。启动方式："
echo "   source .venv/bin/activate"
echo "   uvicorn backend.main:app --host 0.0.0.0 --port 8080"
echo "访问 http://localhost:8080"
