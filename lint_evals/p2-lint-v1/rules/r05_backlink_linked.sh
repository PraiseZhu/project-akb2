#!/usr/bin/env bash
# r05_backlink_linked — check interface backlink_status = linked
# severity: hard_fail (cc-akb-search exception: handoff) | auto_decidable: true
# Requires: emit_finding function (defined in run-lint.sh)

r05_backlink_linked() {
  local slug="$1"
  local iface="$2"

  if [ ! -f "$iface" ]; then
    emit_finding "$slug" "r05" "fail" "hard_fail" "interface file missing; cannot check backlink_status" ""
    return
  fi

  local bs
  bs=$(grep -m1 '^backlink_status:' "$iface" 2>/dev/null | awk '{print $2}')

  if [ "$bs" = "linked" ]; then
    emit_finding "$slug" "r05" "pass" "hard_fail" "backlink_status: linked" ""
  elif [ "$bs" = "pending" ]; then
    if [ "$slug" = "cc-akb-search" ]; then
      # cc-akb-search exception: blocked candidate, mcp__akb-search not mounted → expected pending
      emit_finding "$slug" "r05" "handoff" "handoff" "backlink_status: pending [cc-akb-search blocked candidate]" "cc-akb-search: mcp__akb-search not mounted (per P1a); pending is expected state; not a hard fail"
    else
      emit_finding "$slug" "r05" "fail" "hard_fail" "backlink_status: pending (expected: linked)" ""
    fi
  else
    if [ -z "$bs" ]; then
      emit_finding "$slug" "r05" "fail" "hard_fail" "backlink_status field missing (expected: linked)" ""
    else
      emit_finding "$slug" "r05" "fail" "hard_fail" "backlink_status: '$bs' (expected: linked)" ""
    fi
  fi
}
