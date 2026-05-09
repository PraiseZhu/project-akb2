#!/usr/bin/env bash
# r06_active_needs_l1 — active tools must have eval_level >= L1
# severity: hard_fail | auto_decidable: true
# Requires: emit_finding function (defined in run-lint.sh)

r06_active_needs_l1() {
  local slug="$1"
  local toolcard="$2"
  local evalfile="$3"

  if [ ! -f "$toolcard" ]; then
    emit_finding "$slug" "r06" "fail" "hard_fail" "tool card missing; cannot check status" ""
    return
  fi

  if [ ! -f "$evalfile" ]; then
    emit_finding "$slug" "r06" "fail" "hard_fail" "eval file missing; cannot check eval_level" ""
    return
  fi

  local status
  status=$(grep -m1 '^status:' "$toolcard" 2>/dev/null | awk '{print $2}')

  local level
  level=$(grep -m1 '^eval_level:' "$evalfile" 2>/dev/null | awk '{print $2}')

  if [ "$status" = "active" ] && [ "$level" = "L0" ]; then
    emit_finding "$slug" "r06" "fail" "hard_fail" "status=active but eval_level=L0 (requires >=L1)" ""
  else
    emit_finding "$slug" "r06" "pass" "hard_fail" "status=$status eval_level=$level (ok)" ""
  fi
}
