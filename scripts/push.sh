#!/usr/bin/env bash
cd "$(dirname "$0")/.."
RUN=${1:?run}
MSG=${2:-update}
BR=$(git rev-parse --abbrev-ref HEAD)
git add -A "results/$RUN" 2>/dev/null
git commit -q -m "results: $RUN $MSG $(date -u +%Y-%m-%dT%H:%MZ)" >/dev/null 2>&1 || true
for i in 1 2 3 4 5; do
  if git pull -q --rebase --autostash origin "$BR" && git push -q origin "HEAD:$BR"; then echo "[push] ok ($MSG)"; exit 0; fi
  sleep $((20 * i))
done
echo "[push] failed ($MSG)"
exit 1
