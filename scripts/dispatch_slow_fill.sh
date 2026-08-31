#!/usr/bin/env bash
# Trigger the slow-fill workflow every N seconds (default 60).
#
# GitHub Actions `schedule` cannot run faster than every 5 minutes, so this
# script drives the workflow via the API instead. It dispatches only when no
# run of the workflow is already in progress/queued, so runs never overlap and
# the LLM quota is spread out one small batch at a time.
#
# Usage:
#   scripts/dispatch_slow_fill.sh [interval_seconds]
#   INTERVAL=60 MAX_ROWS=3 TABS="diamond_stock jewellery_stock" scripts/dispatch_slow_fill.sh
set -euo pipefail

REPO="dhruvalshah3557-droid/google-sheet-data-fill"
WORKFLOW="slow-fill.yml"
INTERVAL="${INTERVAL:-${1:-60}}"
MAX_ROWS="${MAX_ROWS:-3}"
TABS="${TABS:-diamond_stock jewellery_stock full_stock}"

get_token() {
  printf 'protocol=https\nhost=github.com\n' | git credential fill | sed -n 's/^password=//p'
}

run_in_progress() {
  local token="$1"
  local status
  status=$(curl -s -H "Authorization: Bearer $token" -H "Accept: application/vnd.github+json" \
    "https://api.github.com/repos/$REPO/actions/workflows/$WORKFLOW/runs?per_page=1" |
    python3 -c "import sys,json; print(json.load(sys.stdin)['workflow_runs'][0]['status'])" 2>/dev/null || echo "none")
  [ "$status" = "in_progress" ] || [ "$status" = "queued" ]
}

dispatch() {
  local token="$1"
  local payload
  payload=$(python3 -c "
import json,sys
print(json.dumps({'ref':'main','inputs':{'max_rows':'$MAX_ROWS','tabs':'$TABS'}}))
")
  curl -s -o /dev/null -w "%{http_code}" -X POST \
    -H "Authorization: Bearer $token" -H "Accept: application/vnd.github+json" \
    "https://api.github.com/repos/$REPO/actions/workflows/$WORKFLOW/dispatches" \
    -d "$payload"
}

echo "Dispatcher started: interval=${INTERVAL}s max_rows=${MAX_ROWS} tabs='${TABS}'"

while true; do
  TOKEN=$(get_token)
  if run_in_progress "$TOKEN"; then
    echo "$(date -u +%FT%TZ) run in progress/queued, skipping dispatch"
  else
    CODE=$(dispatch "$TOKEN")
    echo "$(date -u +%FT%TZ) dispatched -> HTTP $CODE"
  fi
  unset TOKEN
  sleep "$INTERVAL"
done
