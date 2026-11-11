#!/usr/bin/env bash
cd "$(dirname "$0")/.."
CFG=${1:-configs/pilot.yaml}
mkdir -p logs
if [ -f logs/pipeline.pid ] && kill -0 "$(cat logs/pipeline.pid)" 2>/dev/null; then echo "already running: pid $(cat logs/pipeline.pid)"; exit 1; fi
setsid nohup bash scripts/pipeline.sh "$CFG" > "logs/launch_$(date +%Y%m%d_%H%M%S).log" 2>&1 < /dev/null &
echo $! > logs/pipeline.pid
echo "started pid $(cat logs/pipeline.pid) for $CFG"
