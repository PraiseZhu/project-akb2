#!/usr/bin/env bash
# =============================================================================
# run-lint.sh — AKB2.0 P2 Toolbox Structure Lint v0
# Scope: current 5 tools × 5件套 (25 files) + 8 fixture boundary cases
# Read-only: grep / find / shasum / cat only. ZERO mutations.
# Exit 0 always (lint detect only, not blocking).
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
AKB_ROOT="/Users/praise/Library/Mobile Documents/iCloud~md~obsidian/Documents/Praise Space/AKB2.0"
FIXTURES_DIR="$SCRIPT_DIR/fixtures"
OUTPUTS_DIR="$SCRIPT_DIR/outputs"
SKILL_BASE="$HOME/.codex/skills"

# 5 tools (space-separated, bash 3.2 compatible — no declare -A)
TOOLS="cc-executable-plan cc-review cc-local-llm cc-skill-stocktake cc-akb-search"

# ---------------------------------------------------------------------------
# Preflight: verify required tools
# ---------------------------------------------------------------------------
preflight_check() {
  local missing_tools=""
  for cmd in jq shasum bash find grep awk sed; do
    command -v "$cmd" >/dev/null 2>&1 || missing_tools="${missing_tools}${cmd} "
  done
  if [ -n "$missing_tools" ]; then
    echo "[PREFLIGHT ERROR] Missing required commands: $(echo "$missing_tools" | sed 's/[[:space:]]*$//')" >&2
    exit 1
  fi
}

# ---------------------------------------------------------------------------
# emit_finding — write one JSON finding object to stdout
# Args: slug rule_id status severity evidence reviewer_note
# ---------------------------------------------------------------------------
emit_finding() {
  local slug="$1" rule="$2" status="$3" severity="$4" evidence="$5" note="$6"
  # Escape double quotes for JSON
  evidence=$(echo "$evidence" | sed 's/\\/\\\\/g; s/"/\\"/g')
  note=$(echo "$note" | sed 's/\\/\\\\/g; s/"/\\"/g')
  printf '{"tool_slug":"%s","rule_id":"%s","status":"%s","severity":"%s","evidence":"%s","reviewer_note":"%s"}\n' \
    "$slug" "$rule" "$status" "$severity" "$evidence" "$note"
}

# ---------------------------------------------------------------------------
# Source rule functions
# ---------------------------------------------------------------------------
. "$SCRIPT_DIR/rules/r01_five_file_present.sh"
. "$SCRIPT_DIR/rules/r02_status_consistency.sh"
. "$SCRIPT_DIR/rules/r03_workspace_path_exists.sh"
. "$SCRIPT_DIR/rules/r04_sha256_match.sh"
. "$SCRIPT_DIR/rules/r05_backlink_linked.sh"
. "$SCRIPT_DIR/rules/r06_active_needs_l1.sh"
. "$SCRIPT_DIR/rules/r07_eval_freshness.sh"
. "$SCRIPT_DIR/rules/r08_automation_allowed_false.sh"

# ---------------------------------------------------------------------------
# run_rule_by_id — dispatch fixture rule by ID
# Args: rule_id fixture_dir
# ---------------------------------------------------------------------------
run_rule_by_id() {
  local rule_id="$1" fixture_dir="$2"
  local slug="fixture-tool"
  local iface="$fixture_dir/toolbox/interfaces/$slug.md"
  local toolcard="$fixture_dir/toolbox/tools/$slug.md"
  local registry="$fixture_dir/toolbox/registry/$slug.md"
  local evalfile="$fixture_dir/toolbox/evals/$slug.md"
  local wikifile="$fixture_dir/wiki/tools/$slug.md"

  case "$rule_id" in
    r01) r01_five_file_present  "$slug" "$iface" "$toolcard" "$registry" "$evalfile" "$wikifile" ;;
    r02) r02_status_consistency "$slug" "$iface" "$toolcard" "$registry" "$evalfile" "$wikifile" ;;
    r03) r03_workspace_path_exists "$slug" "$iface" ;;
    r04) r04_sha256_match       "$slug" "$iface" "$evalfile" ;;
    r05) r05_backlink_linked    "$slug" "$iface" ;;
    r06) r06_active_needs_l1   "$slug" "$toolcard" "$evalfile" ;;
    r07) r07_eval_freshness     "$slug" "$evalfile" ;;
    r08) r08_automation_allowed_false "$slug" "$iface" "$toolcard" "$registry" "$evalfile" "$wikifile" ;;
    *)   emit_finding "fixture-tool" "$rule_id" "fail" "hard_fail" "unknown rule_id: $rule_id" "" ;;
  esac
}

# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
preflight_check

mkdir -p "$OUTPUTS_DIR" "$OUTPUTS_DIR/case-results"
LINT_RUN_AT="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
FINDINGS_TMP="$OUTPUTS_DIR/.findings_$$.ndjson"
> "$FINDINGS_TMP"

echo "=== AKB2.0 P2 Toolbox Structure Lint v0 ==="
echo "Run at: $LINT_RUN_AT"
echo ""
echo "--- Part 1: Lint current 5 tools ---"

# ---------------------------------------------------------------------------
# PART 1: Run all 8 rules × 5 tools
# ---------------------------------------------------------------------------
for slug in $TOOLS; do
  iface="$AKB_ROOT/toolbox/interfaces/$slug.md"
  toolcard="$AKB_ROOT/toolbox/tools/$slug.md"
  registry="$AKB_ROOT/toolbox/registry/$slug.md"
  evalfile="$AKB_ROOT/toolbox/evals/$slug.md"
  wikifile="$AKB_ROOT/wiki/tools/$slug.md"

  r01_five_file_present  "$slug" "$iface" "$toolcard" "$registry" "$evalfile" "$wikifile" >> "$FINDINGS_TMP"
  r02_status_consistency "$slug" "$iface" "$toolcard" "$registry" "$evalfile" "$wikifile" >> "$FINDINGS_TMP"
  r03_workspace_path_exists "$slug" "$iface"                                              >> "$FINDINGS_TMP"
  r04_sha256_match       "$slug" "$iface" "$evalfile"                                    >> "$FINDINGS_TMP"
  r05_backlink_linked    "$slug" "$iface"                                                 >> "$FINDINGS_TMP"
  r06_active_needs_l1   "$slug" "$toolcard" "$evalfile"                                   >> "$FINDINGS_TMP"
  r07_eval_freshness     "$slug" "$evalfile"                                              >> "$FINDINGS_TMP"
  r08_automation_allowed_false "$slug" "$iface" "$toolcard" "$registry" "$evalfile" "$wikifile" >> "$FINDINGS_TMP"

  echo "  ✓ $slug"
done

# ---------------------------------------------------------------------------
# Build outputs/lint-current-5-tools.json
# ---------------------------------------------------------------------------
jq -s --arg run_at "$LINT_RUN_AT" '{
  lint_run_at: $run_at,
  scope: "current_5_tools",
  summary: {
    tools_scanned: 5,
    rules_evaluated: 8,
    total_findings: (. | length),
    hard_fail_findings: ([.[] | select(.status == "fail" and .severity == "hard_fail")] | length),
    handoff_findings: ([.[] | select(.status == "handoff")] | length),
    pass_count: ([.[] | select(.status == "pass")] | length)
  },
  findings: .
}' "$FINDINGS_TMP" > "$OUTPUTS_DIR/lint-current-5-tools.json"

# ---------------------------------------------------------------------------
# Build outputs/review-handoff.md
# ---------------------------------------------------------------------------
cat > "$OUTPUTS_DIR/review-handoff.md" <<HANDOFF_HEADER
---
generated_at: $LINT_RUN_AT
scope: current_5_tools
lint_run: p2-toolbox-structure-lint-v0
---

# Lint v0 — Reviewer Handoff Items

Items below require human review. These are NOT hard fails — they represent ambiguous states that need contextual judgment.

HANDOFF_HEADER

HANDOFF_COUNT=$(jq '[.findings[] | select(.status == "handoff")] | length' "$OUTPUTS_DIR/lint-current-5-tools.json")

if [ "$HANDOFF_COUNT" -gt 0 ]; then
  echo "| tool_slug | rule_id | evidence | reviewer_note |" >> "$OUTPUTS_DIR/review-handoff.md"
  echo "|-----------|---------|----------|---------------|" >> "$OUTPUTS_DIR/review-handoff.md"
  jq -r '.findings[] | select(.status == "handoff") | "| \(.tool_slug) | \(.rule_id) | \(.evidence) | \(.reviewer_note) |"' \
    "$OUTPUTS_DIR/lint-current-5-tools.json" >> "$OUTPUTS_DIR/review-handoff.md"
else
  echo "_No handoff items — all findings are pass or hard_fail._" >> "$OUTPUTS_DIR/review-handoff.md"
fi

echo "" >> "$OUTPUTS_DIR/review-handoff.md"
echo "## Resolution Guide" >> "$OUTPUTS_DIR/review-handoff.md"
echo "" >> "$OUTPUTS_DIR/review-handoff.md"
echo "- **r02 status split**: Confirm if split is intentional transition state (wiki active + 4件套 candidate is acceptable). If yes: no action needed. If no: update 5件套 to consistent status." >> "$OUTPUTS_DIR/review-handoff.md"
echo "- **r05 cc-akb-search pending**: Expected due to blocked candidate / mcp__akb-search not mounted. No action until mcp__akb-search is mounted." >> "$OUTPUTS_DIR/review-handoff.md"
echo "- **r07 eval stale**: Review if stale threshold is acceptable given tool state. New tools are naturally stale; running L1 eval resolves this." >> "$OUTPUTS_DIR/review-handoff.md"

# ---------------------------------------------------------------------------
# Print Part 1 summary
# ---------------------------------------------------------------------------
echo ""
echo "Part 1 results:"
jq -r '"  tools_scanned:       \(.summary.tools_scanned)
  rules_evaluated:     \(.summary.rules_evaluated)
  total_findings:      \(.summary.total_findings)
  hard_fail_findings:  \(.summary.hard_fail_findings)
  handoff_findings:    \(.summary.handoff_findings)
  pass_count:          \(.summary.pass_count)"' "$OUTPUTS_DIR/lint-current-5-tools.json"

if [ "$(jq '.summary.hard_fail_findings' "$OUTPUTS_DIR/lint-current-5-tools.json")" -gt 0 ]; then
  echo ""
  echo "  [!] HARD FAIL findings:"
  jq -r '.findings[] | select(.status == "fail") | "      \(.tool_slug) \(.rule_id): \(.evidence)"' \
    "$OUTPUTS_DIR/lint-current-5-tools.json"
fi

# ---------------------------------------------------------------------------
# PART 2: Fixture boundary case tests
# ---------------------------------------------------------------------------
echo ""
echo "--- Part 2: Fixture boundary cases ---"

FIXTURE_PASS=0
FIXTURE_FAIL=0

for fixture_dir in "$FIXTURES_DIR"/*/; do
  [ -d "$fixture_dir" ] || continue
  case_name="$(basename "$fixture_dir")"
  expected_json="$fixture_dir/expected.json"
  [ -f "$expected_json" ] || continue

  trigger_rule="$(jq -r '.trigger_rule'         "$expected_json")"
  expected_status="$(jq -r '.expected_status'   "$expected_json")"
  expected_severity="$(jq -r '.expected_severity' "$expected_json")"
  desc="$(jq -r '.description'                  "$expected_json")"

  # Run trigger rule against fixture (capture first finding line)
  actual_json="$(run_rule_by_id "$trigger_rule" "$fixture_dir" | head -1)"

  if [ -z "$actual_json" ]; then
    actual_status="error"
    actual_severity="error"
    actual_evidence="rule produced no output"
    test_result="fail"
  else
    actual_status="$(echo "$actual_json" | jq -r '.status')"
    actual_severity="$(echo "$actual_json" | jq -r '.severity')"
    actual_evidence="$(echo "$actual_json" | jq -r '.evidence')"
    if [ "$actual_status" = "$expected_status" ]; then
      test_result="pass"
    else
      test_result="fail"
    fi
  fi

  mkdir -p "$OUTPUTS_DIR/case-results/$case_name"

  jq -n \
    --arg fixture        "$case_name" \
    --arg trigger_rule   "$trigger_rule" \
    --arg desc           "$desc" \
    --arg exp_status     "$expected_status" \
    --arg exp_severity   "$expected_severity" \
    --arg act_status     "$actual_status" \
    --arg act_severity   "$actual_severity" \
    --arg act_evidence   "$actual_evidence" \
    --arg test_result    "$test_result" \
    '{
      fixture:           $fixture,
      trigger_rule:      $trigger_rule,
      description:       $desc,
      expected_status:   $exp_status,
      expected_severity: $exp_severity,
      actual_status:     $act_status,
      actual_severity:   $act_severity,
      actual_evidence:   $act_evidence,
      test_result:       $test_result,
      assertion:         (if $test_result == "pass"
                          then "PASS: actual=\($act_status) matches expected=\($exp_status)"
                          else "FAIL: actual=\($act_status) expected=\($exp_status)"
                          end)
    }' > "$OUTPUTS_DIR/case-results/$case_name/result.json"

  if [ "$test_result" = "pass" ]; then
    FIXTURE_PASS=$((FIXTURE_PASS + 1))
    echo "  ✓ $case_name → $actual_status (expected $expected_status)"
  else
    FIXTURE_FAIL=$((FIXTURE_FAIL + 1))
    echo "  ✗ $case_name → actual=$actual_status expected=$expected_status"
    echo "    evidence: $actual_evidence"
  fi
done

# ---------------------------------------------------------------------------
# Final summary
# ---------------------------------------------------------------------------
echo ""
echo "=== Lint v0 Complete ==="
echo "Part 1 (current 5 tools): $(jq '.summary.hard_fail_findings' "$OUTPUTS_DIR/lint-current-5-tools.json") hard fail, $(jq '.summary.handoff_findings' "$OUTPUTS_DIR/lint-current-5-tools.json") handoff, $(jq '.summary.pass_count' "$OUTPUTS_DIR/lint-current-5-tools.json") pass"
echo "Part 2 (fixture cases):   $FIXTURE_PASS/$((FIXTURE_PASS + FIXTURE_FAIL)) pass"
echo ""
echo "Outputs:"
echo "  $OUTPUTS_DIR/lint-current-5-tools.json"
echo "  $OUTPUTS_DIR/review-handoff.md"
echo "  $OUTPUTS_DIR/case-results/<case>/result.json (8 cases)"

# Cleanup temp
rm -f "$FINDINGS_TMP"

exit 0
