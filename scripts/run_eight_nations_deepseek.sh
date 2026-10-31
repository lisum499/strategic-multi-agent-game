#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"
MODEL="${MODEL:-deepseek-flash}"
MAX_TOKENS="${MAX_TOKENS:-2800}"
TIMEOUT="${TIMEOUT:-120}"
RETRIES="${RETRIES:-3}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "找不到 Python：$PYTHON_BIN。请先创建 .venv，或设置 PYTHON_BIN。" >&2
  exit 1
fi

if [[ -z "${DEEPSEEK_API_KEY:-}" ]]; then
  echo "未设置 DEEPSEEK_API_KEY。" >&2
  exit 1
fi

cd "$ROOT_DIR"
export PYTHONPATH="$ROOT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"

if [[ "${1:-}" == "--resume" ]]; then
  if [[ -z "${2:-}" ]]; then
    echo "用法：$0 --resume runs/实验目录" >&2
    exit 2
  fi
  exec "$PYTHON_BIN" -m strategic_game.cli run \
    --resume "$2" \
    --provider deepseek \
    --model "$MODEL" \
    --max-tokens "$MAX_TOKENS" \
    --timeout "$TIMEOUT" \
    --retries "$RETRIES"
fi

ROUNDS="${1:-${ROUNDS:-30}}"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT_DIR="${2:-${OUT_DIR:-runs/eight-nations-deepseek-$STAMP}}"

exec "$PYTHON_BIN" -m strategic_game.cli run \
  --provider deepseek \
  --model "$MODEL" \
  --scenario src/strategic_game/scenarios/eight_nations_dawn_strait.json \
  --rounds "$ROUNDS" \
  --out "$OUT_DIR" \
  --max-tokens "$MAX_TOKENS" \
  --timeout "$TIMEOUT" \
  --retries "$RETRIES"
