#!/usr/bin/env bash
# r04_sha256_match — check workspace_version sha12 matches actual SKILL.md sha256
# severity: hard_fail | auto_decidable: true
# Requires: emit_finding function (defined in run-lint.sh)

r04_sha256_match() {
  local slug="$1"
  local iface="$2"
  local evalfile="$3"

  if [ ! -f "$iface" ]; then
    emit_finding "$slug" "r04" "fail" "hard_fail" "interface file missing; cannot check sha256" ""
    return
  fi

  # Extract workspace_path (handles paths with spaces)
  local wp
  wp=$(grep -m1 '^workspace_path:' "$iface" 2>/dev/null | sed 's/^workspace_path:[[:space:]]*//')
  wp="${wp/#\~/$HOME}"

  if [ -z "$wp" ]; then
    emit_finding "$slug" "r04" "fail" "hard_fail" "workspace_path field missing; cannot compute sha" ""
    return
  fi

  if [ ! -f "$wp" ]; then
    emit_finding "$slug" "r04" "fail" "hard_fail" "workspace_path not found ($wp); sha check skipped" ""
    return
  fi

  # Compute actual sha12 of the SKILL.md (or workspace file)
  local actual_sha
  actual_sha=$(shasum -a 256 "$wp" 2>/dev/null | cut -c1-12)

  if [ -z "$actual_sha" ]; then
    emit_finding "$slug" "r04" "fail" "hard_fail" "shasum failed on $wp" ""
    return
  fi

  # Extract sha12 from interface workspace_version field
  # Format: "sha256:5af73fdadd91 mtime:..." or sha256:5af73fdadd91 mtime:...
  local iface_sha
  iface_sha=$(grep -m1 '^workspace_version:' "$iface" 2>/dev/null | grep -oE '[0-9a-f]{12}' | head -1)

  local failures=""

  if [ -z "$iface_sha" ]; then
    failures="${failures}interface:no_sha12_in_workspace_version "
  elif [ "$iface_sha" != "$actual_sha" ]; then
    failures="${failures}interface:field=${iface_sha},actual=${actual_sha} "
  fi

  # Also check eval file if it has workspace_version
  if [ -f "$evalfile" ]; then
    local eval_sha
    eval_sha=$(grep -m1 '^workspace_version:' "$evalfile" 2>/dev/null | grep -oE '[0-9a-f]{12}' | head -1)
    if [ -n "$eval_sha" ] && [ "$eval_sha" != "$actual_sha" ]; then
      failures="${failures}eval:field=${eval_sha},actual=${actual_sha} "
    fi
  fi

  if [ -n "$failures" ]; then
    local f_trimmed
    f_trimmed=$(echo "$failures" | sed 's/[[:space:]]*$//')
    emit_finding "$slug" "r04" "fail" "hard_fail" "sha12 mismatch: $f_trimmed" ""
  else
    emit_finding "$slug" "r04" "pass" "hard_fail" "sha12 match: $actual_sha" ""
  fi
}
