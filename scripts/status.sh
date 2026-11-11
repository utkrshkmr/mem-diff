#!/usr/bin/env bash
cd "$(dirname "$0")/.."
if [ -f logs/pipeline.pid ] && kill -0 "$(cat logs/pipeline.pid)" 2>/dev/null; then echo "running: pid $(cat logs/pipeline.pid)"; else echo "not running"; fi
for d in results/*/; do [ -f "$d/episodes.jsonl" ] && echo "$d $(wc -l < "$d/episodes.jsonl") episodes"; done
L=$(ls -t logs/pipeline_*.log results/*/pipeline.log 2>/dev/null | head -1); [ -n "$L" ] && tail -5 "$L"
nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv,noheader 2>/dev/null
