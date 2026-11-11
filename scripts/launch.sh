#!/usr/bin/env bash
cd "$(dirname "$0")/.."
CFG=${1:-configs/pilot.yaml}
mkdir -p logs
git config pull.rebase true
if [ "${MD_NO_UPDATE:-0}" != 1 ]; then
  if git pull -q --autostash 2>/tmp/md_pull.$$; then echo "code: $(git log --oneline -1)"; else echo "warning: could not update code ($(tail -1 /tmp/md_pull.$$)); running $(git log --oneline -1)"; fi
  rm -f /tmp/md_pull.$$
fi
if [ -f logs/pipeline.pid ] && kill -0 "$(cat logs/pipeline.pid)" 2>/dev/null; then echo "already running: pid $(cat logs/pipeline.pid)"; exit 1; fi
setsid nohup bash scripts/pipeline.sh "$CFG" > "logs/launch_$(date +%Y%m%d_%H%M%S).log" 2>&1 < /dev/null &
echo $! > logs/pipeline.pid
echo "started pid $(cat logs/pipeline.pid) for $CFG"
RUN=$(python3 -c "import yaml;print(yaml.safe_load(open('$CFG'))['run'])" 2>/dev/null || grep -m1 '^run:' "$CFG" | awk '{print $2}')
for i in $(seq 1 60); do [ -f "logs/pipeline_$RUN.log" ] && break; sleep 1; done
echo "log: logs/pipeline_$RUN.log (copied to results/$RUN/pipeline.log on each push)"
