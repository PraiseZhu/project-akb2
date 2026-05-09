#!/usr/bin/env bash
# r02_status_consistency — check status field is consistent across all 5件套 files
# severity: handoff (not fail) | auto_decidable: false
# Requires: emit_finding function (defined in run-lint.sh)

r02_status_consistency() {
  local slug="$1"
  local iface="$2" toolcard="$3" registry="$4" evalfile="$5" wikifile="$6"

  local statuses=""
  for f in "$iface" "$toolcard" "$registry" "$evalfile" "$wikifile"; do
    if [ -f "$f" ]; then
      local s
      s=$(grep -m1 '^status:' "$f" 2>/dev/null | awk '{print $2}')
      if [ -n "$s" ]; then
        statuses="${statuses}${s} "
      fi
    fi
  done

  if [ -z "$(echo "$statuses" | tr -d ' ')" ]; then
    emit_finding "$slug" "r02" "handoff" "handoff" "no status fields found in any 5件套 file" "check if status field is present"
    return
  fi

  # Count unique status values (bash 3.2 compatible)
  local unique_count
  unique_count=$(echo "$statuses" | tr ' ' '\n' | grep -v '^$' | sort -u | wc -l | tr -d ' ')

  if [ "$unique_count" -gt 1 ]; then
    local vals
    vals=$(echo "$statuses" | tr ' ' '\n' | grep -v '^$' | sort -u | tr '\n' ',' | sed 's/,$//')
    emit_finding "$slug" "r02" "handoff" "handoff" "status split: {$vals}" "status inconsistency across 5件套; review if intentional transition state (eg wiki active + 4件套 candidate)"
  else
    local val
    val=$(echo "$statuses" | tr ' ' '\n' | grep -v '^$' | head -1)
    emit_finding "$slug" "r02" "pass" "handoff" "status consistent: $val (all 5件套)" ""
  fi
}
