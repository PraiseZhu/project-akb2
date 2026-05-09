#!/usr/bin/env bash
# r03_workspace_path_exists — check interface workspace_path file actually exists
# severity: hard_fail | auto_decidable: true
# Requires: emit_finding function (defined in run-lint.sh)

r03_workspace_path_exists() {
  local slug="$1"
  local iface="$2"

  if [ ! -f "$iface" ]; then
    emit_finding "$slug" "r03" "fail" "hard_fail" "interface file missing; cannot check workspace_path" ""
    return
  fi

  # Use sed to handle paths with spaces (awk would split on spaces)
  local wp
  wp=$(grep -m1 '^workspace_path:' "$iface" 2>/dev/null | sed 's/^workspace_path:[[:space:]]*//')

  if [ -z "$wp" ]; then
    emit_finding "$slug" "r03" "fail" "hard_fail" "workspace_path field missing in interface" ""
    return
  fi

  # Expand ~ to $HOME (bash 3.2 compatible)
  wp="${wp/#\~/$HOME}"

  if [ -f "$wp" ]; then
    emit_finding "$slug" "r03" "pass" "hard_fail" "workspace_path exists: $wp" ""
  else
    emit_finding "$slug" "r03" "fail" "hard_fail" "workspace_path not found: $wp" ""
  fi
}
