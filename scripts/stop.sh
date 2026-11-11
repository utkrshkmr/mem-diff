#!/usr/bin/env bash
cd "$(dirname "$0")/.."
for c in $(docker ps -a --format '{{.Names}}' 2>/dev/null | grep '^md-'); do docker rm -f "$c" >/dev/null && echo "[stop] $c"; done
for p in logs/vllm_*.pid; do [ -f "$p" ] && kill "$(cat "$p")" 2>/dev/null && echo "[stop] $(basename "$p" .pid)"; rm -f "$p"; done
if [ "${1:-}" = all ] && [ -f logs/pipeline.pid ]; then pkill -TERM -s "$(cat logs/pipeline.pid)" 2>/dev/null; echo "[stop] pipeline"; fi
