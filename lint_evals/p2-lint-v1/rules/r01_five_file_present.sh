#!/usr/bin/env bash
# r01_five_file_present — check all 5件套 files exist for a tool
# severity: hard_fail | auto_decidable: true
# Requires: emit_finding function (defined in run-lint.sh)

r01_five_file_present() {
  local slug="$1"
  local iface="$2" toolcard="$3" registry="$4" evalfile="$5" wikifile="$6"

  local missing=""
  [ -f "$iface" ]     || missing="${missing}interfaces/$slug.md "
  [ -f "$toolcard" ]  || missing="${missing}tools/$slug.md "
  [ -f "$registry" ]  || missing="${missing}registry/$slug.md "
  [ -f "$evalfile" ]  || missing="${missing}evals/$slug.md "
  [ -f "$wikifile" ]  || missing="${missing}wiki/tools/$slug.md "

  if [ -n "$missing" ]; then
    # trim trailing space
    local m
    m=$(echo "$missing" | sed 's/[[:space:]]*$//')
    emit_finding "$slug" "r01" "fail" "hard_fail" "missing: $m" ""
  else
    emit_finding "$slug" "r01" "pass" "hard_fail" "5/5 files present" ""
  fi
}
