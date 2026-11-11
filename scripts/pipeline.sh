#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")/.."
CFG=${1:-configs/pilot.yaml}
export NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost PYTHONUNBUFFERED=1
if [ ! -x .venv/bin/python ]; then python3 -m venv .venv && .venv/bin/pip install -q --upgrade pip && .venv/bin/pip install -q -r requirements.txt || exit 1; fi
export PY=.venv/bin/python
RUN=$($PY -c "import yaml;print(yaml.safe_load(open('$CFG'))['run'])")
OUT=results/$RUN
mkdir -p "$OUT" logs
LOG=logs/pipeline_$RUN.log
export MD_LOG=$LOG
exec > >(tee -a "$LOG") 2>&1
echo "[pipeline] $(date -u) config=$CFG run=$RUN host=$(hostname) commit=$(git rev-parse --short HEAD)"
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader 2>/dev/null | sed 's/^/[gpu] /' || echo "[gpu] host nvidia-smi unavailable; sampling through the model containers"
$PY -m memdiff.cli plan "$CFG" | tail -2
finish() {
  for f in logs/vllm_*.log; do [ -f "$f" ] && tail -200 "$f" > "$OUT/$(basename "$f")"; done
  bash scripts/push.sh "$RUN" "$1"
  [ "${MD_KEEP_SERVERS:-0}" = 1 ] || bash scripts/stop.sh
}
bash scripts/serve.sh "$CFG" || { echo "[pipeline] serving failed"; finish serve-failed; exit 1; }
$PY -m memdiff.cli preflight "$CFG" || { echo "[pipeline] preflight failed"; finish preflight-failed; exit 1; }
GS=$($PY -c "import yaml;print(yaml.safe_load(open('$CFG')).get('gpu_sample_s',2))")
[ -f "$OUT/gpu.csv" ] || echo "unix,index,power_w,util_pct,mem_used_mib,sm_clock_mhz,temp_c" > "$OUT/gpu.csv"
Q="--query-gpu=index,power.draw,utilization.gpu,memory.used,clocks.sm,temperature.gpu --format=csv,noheader,nounits"
smi() {
  if nvidia-smi $Q 2>/dev/null; then return; fi
  C=$(docker ps --format '{{.Names}}' 2>/dev/null | grep '^md-' | head -1)
  [ -n "$C" ] && docker exec "$C" nvidia-smi $Q 2>/dev/null
}
( while true; do T=$(date +%s.%N); smi | grep -E '^[0-9]' | sed "s/^/$T,/; s/ //g" >> "$OUT/gpu.csv"; sleep "$GS"; done ) &
SAMPLER=$!
( while sleep "${MD_PUSH_EVERY:-1800}"; do bash scripts/push.sh "$RUN" progress; done ) &
PUSHER=$!
$PY -m memdiff.cli run "$CFG"
RC=$?
kill "$PUSHER" 2>/dev/null
if [ "${MD_RETRY_FAILED:-1}" = 1 ]; then $PY -m memdiff.cli run "$CFG"; fi
kill "$SAMPLER" 2>/dev/null
$PY -m memdiff.cli analyze "$OUT"
echo "[pipeline] done $(date -u) rc=$RC"
finish final
