#!/usr/bin/env bash
# Quick health check for the running observation harness.
#
# Reports:
#   - Whether the monitor wrapper is running (and its PID)
#   - Last heartbeat / signal / outcome line from today's log
#   - Counts of signals + outcomes recorded today

set -u
cd "$(dirname "$0")"
DATE=$(date +%Y%m%d)
SIGNAL_LOG="logs/signals_${DATE}.jsonl"
STDOUT_LOG="logs/monitor_${DATE}.log"

PIDS=$(pgrep -f "monitor.py --threshold" || true)
if [ -z "$PIDS" ]; then
  echo "STATUS: not running"
else
  echo "STATUS: running (pid: $PIDS)"
fi
echo

if [ -f "$STDOUT_LOG" ]; then
  echo "=== last 5 lines from $STDOUT_LOG ==="
  tail -5 "$STDOUT_LOG"
  echo
fi

if [ -f "$SIGNAL_LOG" ]; then
  TOTAL=$(wc -l < "$SIGNAL_LOG" | tr -d ' ')
  SIGNALS=$(grep -c '"chosen_market_title"' "$SIGNAL_LOG" 2>/dev/null || echo 0)
  OUTCOMES=$(grep -c '"type": "outcome"' "$SIGNAL_LOG" 2>/dev/null || echo 0)
  WINS=$(grep -c '"correct": true' "$SIGNAL_LOG" 2>/dev/null || echo 0)
  LOSSES=$(grep -c '"correct": false' "$SIGNAL_LOG" 2>/dev/null || echo 0)
  echo "=== signals_${DATE}.jsonl: $TOTAL lines ==="
  echo "  signals fired:    $SIGNALS"
  echo "  outcomes logged:  $OUTCOMES  (wins: $WINS, losses: $LOSSES)"
  if [ "$OUTCOMES" -gt 0 ]; then
    PAYOFF=$(python3 -c "
import json, sys
total = 0.0
for line in open('$SIGNAL_LOG'):
    try:
        r = json.loads(line)
        if r.get('type') == 'outcome':
            total += r.get('payoff_per_unit', 0.0)
    except: pass
print(f'{total:+.4f}')
" 2>/dev/null)
    echo "  cumulative payoff per unit: $PAYOFF"
  fi
else
  echo "=== no signals log for today yet ==="
fi
