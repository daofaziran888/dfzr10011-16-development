#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
if [ -x .venv/bin/python ]; then
  .venv/bin/pip install -q -r requirements.txt
  exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
fi
# 预置的 .venv 只是依赖目录（没有 python/pip 可执行文件）时，
# 直接把它加进 PYTHONPATH，用系统解释器起服务。
export PYTHONPATH="$(pwd)/.venv${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000
