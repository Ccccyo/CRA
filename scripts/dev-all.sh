#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

PYTHON_BIN="${PYTHON_BIN:-services/cra_api/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "缺少后端运行环境，请先按 README 完成首次安装。" >&2
  exit 1
fi

if ! "$PYTHON_BIN" -c "import uvicorn" >/dev/null 2>&1; then
  echo "后端虚拟环境缺少 uvicorn，请先安装 services/cra_api/requirements.txt。" >&2
  exit 1
fi

if [[ ! -x node_modules/.bin/next ]]; then
  echo "缺少前端依赖，请先运行 pnpm install。" >&2
  exit 1
fi

PYTHONPATH=services/cra_api/src "$PYTHON_BIN" -m uvicorn \
  cra_api.main:app --host 127.0.0.1 --port 8001 &
API_PID=$!

cleanup() {
  kill "$API_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

node_modules/.bin/next dev --turbopack -p 3100

