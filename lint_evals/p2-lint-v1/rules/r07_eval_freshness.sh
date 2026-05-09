#!/usr/bin/env bash
# r07_eval_freshness — last_run_at not older than 30 days → handoff (not fail)
# severity: handoff | auto_decidable: false (threshold is debatable)
# Requires: emit_finding function (defined in run-lint.sh)

r07_eval_freshness() {
  local slug="$1"
  local evalfile="$2"

  if [ ! -f "$evalfile" ]; then
    emit_finding "$slug" "r07" "handoff" "handoff" "eval file missing; cannot check last_run_at" "check r01 first"
    return
  fi

  local last_run
  last_run=$(grep -m1 '^last_run_at:' "$evalfile" 2>/dev/null | awk '{print $2}' | tr -d '"')

  if [ -z "$last_run" ]; then
    emit_finding "$slug" "r07" "handoff" "handoff" "last_run_at field missing" "no eval run recorded; treat as never run"
    return
  fi

  # macOS BSD date: parse YYYY-MM-DD
  local last_epoch
  last_epoch=$(date -j -f "%Y-%m-%d" "$last_run" "+%s" 2>/dev/null)

  if [ -z "$last_epoch" ]; then
    emit_finding "$slug" "r07" "handoff" "handoff" "last_run_at=$last_run (cannot parse date; expected YYYY-MM-DD)" "unparseable date; manual review required"
    return
  fi

  local today_epoch
  today_epoch=$(date +%s)

  local days_since
  days_since=$(( (today_epoch - last_epoch) / 86400 ))

  if [ "$days_since" -gt 30 ]; then
    emit_finding "$slug" "r07" "handoff" "handoff" "last_run_at=$last_run (${days_since}d ago; threshold: 30d)" "eval stale; review if acceptable given tool state"
  else
    emit_finding "$slug" "r07" "pass" "handoff" "last_run_at=$last_run (${days_since}d ago; within 30d)" ""
  fi
}
