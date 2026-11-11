#!/usr/bin/env bash
cd "$(dirname "$0")/.."
RUN=${1:?run}
MSG=${2:-update}
BR=$(git rev-parse --abbrev-ref HEAD)
[ -f "logs/pipeline_$RUN.log" ] && cp "logs/pipeline_$RUN.log" "results/$RUN/pipeline.log"
git add -A "results/$RUN" 2>/dev/null
git commit -q -m "results: $RUN $MSG $(date -u +%Y-%m-%dT%H:%MZ)" >/dev/null 2>&1 || true
export GIT_TERMINAL_PROMPT=0
for i in 1 2 3 4 5; do
  if ERR=$( { git fetch -q origin "$BR" && git merge -q --no-edit "origin/$BR" && git push -q origin "HEAD:$BR"; } 2>&1 ); then echo "[push] ok ($MSG)"; exit 0; fi
  echo "[push] attempt $i failed: $(echo "$ERR" | tail -2 | tr '\n' ' ')"
  case "$ERR" in *"could not read Username"*|*"Authentication failed"*|*"Permission denied"*) echo "[push] no git credentials on this machine: run 'gh auth login && gh auth setup-git' or use an SSH remote, then 'bash scripts/push.sh $RUN manual'"; exit 1;; esac
  sleep $((20 * i))
done
echo "[push] failed ($MSG)"
exit 1
