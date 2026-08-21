#!/usr/bin/env bash
# 启动筑听开源本地版（需先运行 scripts/install.sh）
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -d .venv ]]; then
  echo "未找到 .venv，请先运行 bash scripts/install.sh"
  exit 1
fi

# shellcheck disable=SC1091
source .venv/bin/activate
exec uvicorn backend.main:app --host "${JKINCO_HOST:-0.0.0.0}" --port "${JKINCO_PORT:-8080}"
