#!/usr/bin/env bash
# start-bragi.sh — one-command launcher for Bragi-LLM
# Spawns llama-server (port 8081) + bragi-server proxy (port 8080).
# Code Tree or any OpenAI-compatible client connects to :8080 and gets Bragi behaviour.
set -e

# === paths (override via env) ===
LLAMA_BIN="${LLAMA_BIN:-$HOME/llama.cpp/build/bin/llama-server}"
MODEL_PATH="${MODEL_PATH:-$HOME/Documents/Bragi-LLM/c15v-q3km-imat.gguf}"
LLAMA_PORT="${LLAMA_PORT:-8081}"
PROXY_PORT="${BRAGI_PORT:-8080}"
NGL="${NGL:-0}"   # 0 = CPU only, 99 = full GPU
PARALLEL="${PARALLEL:-4}"
CTX="${CTX:-16384}"

# === pre-flight ===
if [ ! -x "$LLAMA_BIN" ]; then
  echo "error: llama-server not found at $LLAMA_BIN"
  echo "  build llama.cpp first, or set LLAMA_BIN env var"
  exit 1
fi
if [ ! -f "$MODEL_PATH" ]; then
  echo "error: model GGUF not found at $MODEL_PATH"
  echo "  download c15v-q3km-imat.gguf from huggingface.co/norika1207-lab/Bragi-LLM-GGUF"
  echo "  or rebuild from source (see README §Setup)"
  exit 1
fi
if ! command -v node >/dev/null 2>&1; then
  echo "error: node not found in PATH"
  exit 1
fi

# kill stale instances
pkill -f "llama-server.*--port $LLAMA_PORT" 2>/dev/null || true
pkill -f "bragi-server.js" 2>/dev/null || true
sleep 1

# === launch llama-server (background) ===
echo "starting llama-server on :$LLAMA_PORT (-ngl $NGL --parallel $PARALLEL -c $CTX)..."
nohup "$LLAMA_BIN" \
  -m "$MODEL_PATH" \
  -ngl "$NGL" \
  -c "$CTX" \
  --parallel "$PARALLEL" \
  --port "$LLAMA_PORT" \
  > "$HOME/.bragi-llama.log" 2>&1 &
LLAMA_PID=$!
echo "  llama-server PID=$LLAMA_PID"

# wait for llama-server to be ready
for i in $(seq 1 30); do
  if curl -sf "http://localhost:$LLAMA_PORT/health" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if ! curl -sf "http://localhost:$LLAMA_PORT/health" >/dev/null 2>&1; then
  echo "error: llama-server failed to start in 30s. tail -20 ~/.bragi-llama.log:"
  tail -20 "$HOME/.bragi-llama.log"
  kill $LLAMA_PID 2>/dev/null || true
  exit 1
fi
echo "  llama-server ready."

# === launch bragi-server proxy (foreground) ===
echo "starting bragi-server on :$PROXY_PORT..."
echo
echo "===================================================="
echo " Bragi-LLM ready."
echo "   - Bragi proxy:    http://localhost:$PROXY_PORT  (OpenAI-compatible)"
echo "   - llama backend:  http://localhost:$LLAMA_PORT"
echo "   - model tag:      bragi-llm"
echo "   - logs:           ~/.bragi-llama.log"
echo
echo " Use with Code Tree:"
echo "   export CODETREE_LOCAL_URL=http://localhost:$PROXY_PORT/v1"
echo "   export CODETREE_LOCAL_MODEL=bragi-llm"
echo "   code-tree ."
echo
echo " Use with any OpenAI-compatible client by pointing it at :$PROXY_PORT"
echo "===================================================="
echo

cd "$(dirname "$0")"
BRAGI_PORT="$PROXY_PORT" BRAGI_LLAMA_URL="http://localhost:$LLAMA_PORT/v1/chat/completions" \
  exec node bragi-server.js
