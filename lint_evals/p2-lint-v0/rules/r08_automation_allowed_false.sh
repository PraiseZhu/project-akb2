#!/usr/bin/env bash
# r08_automation_allowed_false — automation_allowed: true is 0-tolerance P0 gate
# severity: hard_fail (P0) | auto_decidable: true
# Requires: emit_finding function (defined in run-lint.sh)

r08_automation_allowed_false() {
  local slug="$1"
  local iface="$2" toolcard="$3" registry="$4" evalfile="$5" wikifile="$6"

  local hits=""
  for f in "$iface" "$toolcard" "$registry" "$evalfile" "$wikifile"; do
    if [ -f "$f" ]; then
      if grep -q '^automation_allowed: true$' "$f" 2>/dev/null; then
        local fname
        fname=$(basename "$(dirname "$f")")/$(basename "$f")
        hits="${hits}${fname} "
      fi
    fi
  done

  if [ -n "$hits" ]; then
    local h_trimmed
    h_trimmed=$(echo "$hits" | sed 's/[[:space:]]*$//')
    emit_finding "$slug" "r08" "fail" "hard_fail" "automation_allowed: true in: $h_trimmed" "P0 gate violation: 0 tolerance; immediate hard fail"
  else
    emit_finding "$slug" "r08" "pass" "hard_fail" "automation_allowed: true = 0 (all 5件套 clean)" ""
  fi
}
