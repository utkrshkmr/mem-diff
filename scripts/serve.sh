#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")/.."
CFG=${1:?config}
PY=${PY:-python3}
MODE=${MD_SERVE:-docker}
IMAGE=${MD_VLLM_IMAGE:-vllm/vllm-openai:latest}
HF_CACHE=${HF_HOME:-$HOME/.cache/huggingface}
mkdir -p logs
export NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost
$PY -m memdiff.cli models "$CFG" | while read -r NAME HF SERVED PORT UTIL MAXLEN EXTRA; do
  if curl -sf "http://127.0.0.1:$PORT/v1/models" | grep -q "\"$SERVED\""; then
    echo "[serve] $NAME already up on $PORT"
    continue
  fi
  ARGS="$HF --served-model-name $SERVED --gpu-memory-utilization $UTIL --max-model-len $MAXLEN --enable-auto-tool-choice --tool-call-parser hermes --enable-prefix-caching --max-num-seqs ${MD_MAX_SEQS:-128} $EXTRA ${MD_VLLM_EXTRA:-}"
  echo "[serve] starting $NAME ($HF) on $PORT via $MODE"
  if [ "$MODE" = docker ]; then
    docker rm -f "md-$NAME" >/dev/null 2>&1
    docker run -d --name "md-$NAME" --gpus all --ipc=host -p "$PORT:8000" \
      -v "$HF_CACHE:/root/.cache/huggingface" ${HF_TOKEN:+-e HF_TOKEN=$HF_TOKEN} \
      "$IMAGE" $ARGS > /dev/null || { echo "[serve] docker run failed for $NAME"; exit 1; }
    ( docker logs -f "md-$NAME" > "logs/vllm_$NAME.log" 2>&1 & )
  else
    VLLM=${MD_VLLM_BIN:-vllm}
    setsid nohup $VLLM serve $ARGS --port "$PORT" > "logs/vllm_$NAME.log" 2>&1 < /dev/null &
    echo $! > "logs/vllm_$NAME.pid"
  fi
  for i in $(seq 1 ${MD_SERVE_WAIT:-240}); do
    if curl -sf "http://127.0.0.1:$PORT/v1/models" >/dev/null; then echo "[serve] $NAME ready after $((i*15))s"; break; fi
    if [ "$MODE" = docker ] && [ "$(docker inspect -f '{{.State.Running}}' "md-$NAME" 2>/dev/null)" != true ]; then
      echo "[serve] $NAME container exited"; docker logs --tail 60 "md-$NAME"; exit 1
    fi
    sleep 15
  done
  curl -sf "http://127.0.0.1:$PORT/v1/models" >/dev/null || { echo "[serve] $NAME not ready"; tail -60 "logs/vllm_$NAME.log"; exit 1; }
done
