#!/usr/bin/env bash
# run-gate.sh — Automation Gate v0 (dry-run only, read-only)
#
# Usage:
#   bash run-gate.sh --self-test            # run all fixtures, expect all pass (exit 0)
#   bash run-gate.sh --dry-run-current      # evaluate real current state
#   bash run-gate.sh --tool <slug>          # single tool diagnostic
#
# Exit codes:
#   0  at least one tool eligible (dry-run mode only — gate NEVER enables automation)
#   1  all tools blocked
#   2  closure-manifest not found
#   3  invariant violation (automation_allowed=true found globally)
#   4  runner error (bad args, missing fixture files)
#
# IMPORTANT: This script is read-only. It never modifies automation_allowed,
# SKILL.md, tool cards, closure-manifest, or any formal eval files.
# Writes are restricted to outputs/ and run-evidence.md within this directory.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CODEX_BASE="$(cd "$SCRIPT_DIR/../../.." && pwd)"
AKB_ROOT="/Users/praise/Library/Mobile Documents/iCloud~md~obsidian/Documents/Praise Space/AKB2.0"
CLOSURE_MANIFEST="$CODEX_BASE/ai-infra/akb2-toolbox-closure/2026-05-07/closure-manifest.json"
L2_REG_BASE="$CODEX_BASE/ai-infra/akb2-toolbox-evals/l2-regression-v1"
PROMO_BASE="$CODEX_BASE/ai-infra/akb2-toolbox-promotion-gate/v0/outputs"
FIXTURES_DIR="$SCRIPT_DIR/fixtures"
OUTPUTS_DIR="$SCRIPT_DIR/outputs/current"
EXPECTED_MANIFEST_SHA="0fbd53703ff58802299ba8cb04f5290e4b973c124729376dc341f862f06f90cc"

MODE=""
TOOL_FILTER=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --self-test)       MODE="self-test"; shift ;;
    --dry-run-current) MODE="dry-run-current"; shift ;;
    --tool)            MODE="tool"; TOOL_FILTER="$2"; shift 2 ;;
    *) echo "ERROR: unknown arg $1" >&2; exit 4 ;;
  esac
done

[[ -z "$MODE" ]] && { echo "ERROR: specify --self-test, --dry-run-current, or --tool <slug>" >&2; exit 4; }

# ============================================================
# eval_tool: evaluate one tool against all policy conditions
# Returns: blocker_reason (empty = eligible)
# Condition order: L0/L1 first (most fundamental), then status, then L2 evidence
# ============================================================
eval_tool() {
  local slug="$1" eval_level="$2" status="$3" l2_verdict="$4"
  local promo_exists="$5" auto_allowed="$6" runtime_mut="$7"
  local rollback_exists="$8" human_approval="$9"

  # Condition: eval_level (L0 before L1 before active — most fundamental)
  [[ "$eval_level" == "L0" ]] && { echo "blocked_l0_or_below"; return; }
  [[ "$eval_level" != "L2" ]] && { echo "blocked_l1_floor"; return; }

  # Condition: active status
  [[ "$status" != "active" ]] && { echo "blocked_status_not_active"; return; }

  # Condition: L2 regression evidence + verdict
  [[ "$l2_verdict" == "MISSING" ]] && { echo "blocked_no_l2_regression_evidence"; return; }
  [[ "$l2_verdict" == "blocked" ]] && { echo "blocked_l2_verdict_blocked"; return; }
  [[ "$l2_verdict" == "needs_patch" ]] && { echo "blocked_needs_patch"; return; }
  [[ "$l2_verdict" != "recommend_promote_L2" ]] && { echo "blocked_l2_verdict_blocked"; return; }

  # Condition: promotion evidence
  [[ "$promo_exists" != "true" ]] && { echo "blocked_no_promotion_evidence"; return; }

  # Condition: automation_currently_off (per-tool check; global check is separate)
  [[ "$auto_allowed" == "true" ]] && { echo "hard_fail_automation_already_true"; return; }

  # Condition: runtime_mutation_off
  [[ "$runtime_mut" == "true" ]] && { echo "blocked_runtime_mutation_on"; return; }

  # Condition: rollback_target (closure-manifest sha256 intact)
  [[ "$rollback_exists" != "true" ]] && { echo "blocked_no_rollback_target"; return; }

  # Condition: human_approval_marker
  [[ "$human_approval" != "true" ]] && { echo "blocked_no_human_approval"; return; }

  echo ""  # eligible
}

read_card_bool() {
  local card="$1" field="$2"
  [[ -f "$card" ]] && grep -q "^${field}: true" "$card" 2>/dev/null && echo "true" || echo "false"
}

# ============================================================
# Self-test: run all fixtures, compare actual vs expected verdict
# Global AKB grep is skipped (fixture tool-cards drive per-tool check)
# ============================================================
run_self_test() {
  echo "[self-test] Fixtures: $FIXTURES_DIR"
  local pass=0 fail=0

  for fixture_dir in "$FIXTURES_DIR"/*/; do
    [[ -d "$fixture_dir" ]] || continue
    local fname; fname=$(basename "$fixture_dir")
    local expected_file="$fixture_dir/expected.json"

    if [[ ! -f "$expected_file" ]]; then
      echo "[FAIL] $fname: missing expected.json"
      ((fail++)) || true; continue
    fi

    local expected_verdict; expected_verdict=$(jq -r '.global_verdict' "$expected_file" 2>/dev/null)
    local manifest_file="$fixture_dir/manifest.json"

    if [[ ! -f "$manifest_file" ]]; then
      echo "[FAIL] $fname: missing manifest.json"
      ((fail++)) || true; continue
    fi

    local actual_verdict="all_eligible"
    local n_tools=0 n_eligible=0

    while IFS= read -r slug; do
      ((n_tools++)) || true

      local eval_level status
      eval_level=$(jq -r --arg s "$slug" '.tools[] | select(.slug==$s) | .eval_level // "L0"' "$manifest_file" 2>/dev/null)
      status=$(jq -r --arg s "$slug" '.tools[] | select(.slug==$s) | .toolbox_status // "active"' "$manifest_file" 2>/dev/null)
      local human_marker
      human_marker=$(jq -r --arg s "$slug" '.tools[] | select(.slug==$s) | .human_approval_marker // "false"' "$manifest_file" 2>/dev/null)

      local l2_rec="$fixture_dir/l2-recs/$slug/recommendation.json"
      local l2_verdict="MISSING"
      [[ -f "$l2_rec" ]] && l2_verdict=$(jq -r '.verdict // "MISSING"' "$l2_rec" 2>/dev/null)

      local promo_exists="false"
      [[ -d "$fixture_dir/promotion-evidence/$slug" ]] && promo_exists="true"

      local card="$fixture_dir/tool-cards/$slug.md"
      local auto_allowed; auto_allowed=$(read_card_bool "$card" "automation_allowed")
      local runtime_mut; runtime_mut=$(read_card_bool "$card" "runtime_mutation_allowed")

      local rollback_exists="true"
      [[ -f "$fixture_dir/no-rollback-target" ]] && rollback_exists="false"

      local human_approval="$human_marker"
      [[ -f "$fixture_dir/approval/$slug.json" ]] && human_approval="true"
      [[ -f "$fixture_dir/approval.json" ]] && human_approval="true"

      local blocker
      blocker=$(eval_tool "$slug" "$eval_level" "$status" "$l2_verdict" \
        "$promo_exists" "$auto_allowed" "$runtime_mut" "$rollback_exists" "$human_approval")

      if [[ -z "$blocker" ]]; then
        ((n_eligible++)) || true
      else
        actual_verdict="$blocker"
      fi
    done <<< "$(jq -r '.tools[].slug' "$manifest_file" 2>/dev/null)"

    if [[ $n_tools -gt 0 && $n_eligible -eq $n_tools ]]; then
      actual_verdict="all_eligible"
    elif [[ $n_eligible -gt 0 && $n_eligible -lt $n_tools ]]; then
      actual_verdict="partial_eligible"
    fi

    if [[ "$actual_verdict" == "$expected_verdict" ]]; then
      echo "[PASS] $fname (global_verdict=$actual_verdict)"
      ((pass++)) || true
    else
      echo "[FAIL] $fname: expected=$expected_verdict actual=$actual_verdict"
      ((fail++)) || true
    fi
  done

  echo ""
  echo "Self-test: $pass passed, $fail failed"
  [[ $fail -eq 0 ]] && exit 0 || exit 1
}

# ============================================================
# Dry-run current / tool: evaluate real disk state
# ============================================================
run_dry_run_current() {
  if [[ ! -f "$CLOSURE_MANIFEST" ]]; then
    echo "ERROR: closure-manifest not found: $CLOSURE_MANIFEST" >&2
    exit 2
  fi

  # Global invariant guard: automation_allowed=true must be 0
  local auto_count
  auto_count=$( (grep -rn "^automation_allowed: true" \
    "$AKB_ROOT/toolbox/tools" "$AKB_ROOT/wiki/tools" 2>/dev/null || true) \
    | wc -l | tr -d ' ')
  if [[ "$auto_count" != "0" ]]; then
    echo "HARD FAIL: automation_allowed=true found ($auto_count hits) — exit 3" >&2
    exit 3
  fi

  # Closure-manifest sha256
  local actual_sha
  actual_sha=$(/usr/bin/shasum -a 256 "$CLOSURE_MANIFEST" 2>/dev/null | /usr/bin/awk '{print $1}') || actual_sha="MISSING"
  local sha_match="false"
  [[ "$actual_sha" == "$EXPECTED_MANIFEST_SHA" ]] && sha_match="true"

  local per_tool_results=()
  local eligible_tools=()
  local global_verdict="all_eligible"
  local global_blocker=""

  while IFS= read -r tool_obj; do
    local slug eval_level status
    slug=$(echo "$tool_obj" | jq -r '.slug // ""' 2>/dev/null)
    eval_level=$(echo "$tool_obj" | jq -r '.eval_level // "L0"' 2>/dev/null)
    status=$(echo "$tool_obj" | jq -r '.toolbox_status // "active"' 2>/dev/null)
    [[ -z "$slug" ]] && continue
    [[ -n "$TOOL_FILTER" && "$slug" != "$TOOL_FILTER" ]] && continue

    local l2_rec="$L2_REG_BASE/$slug/recommendation.json"
    local l2_verdict="MISSING"
    [[ -f "$l2_rec" ]] && l2_verdict=$(jq -r '.verdict // "MISSING"' "$l2_rec" 2>/dev/null)

    local promo_exists="false"
    [[ -d "$PROMO_BASE/$slug" ]] && promo_exists="true"

    local auto_allowed="false" runtime_mut="false"
    for card in "$AKB_ROOT/toolbox/tools/$slug.md" "$AKB_ROOT/wiki/tools/$slug.md"; do
      [[ "$(read_card_bool "$card" "automation_allowed")" == "true" ]] && auto_allowed="true"
      [[ "$(read_card_bool "$card" "runtime_mutation_allowed")" == "true" ]] && runtime_mut="true"
    done

    local rollback_exists="$sha_match"

    local human_approval="false"
    [[ -f "$SCRIPT_DIR/approvals/$slug.json" ]] && human_approval="true"

    local blocker
    blocker=$(eval_tool "$slug" "$eval_level" "$status" "$l2_verdict" \
      "$promo_exists" "$auto_allowed" "$runtime_mut" "$rollback_exists" "$human_approval")

    local evidence_json
    evidence_json=$(jq -n \
      --arg lp "$l2_rec" --arg lv "$l2_verdict" --arg el "$eval_level" \
      '{"l2_recommendation_path":$lp,"verdict":$lv,"eval_level":$el}')

    if [[ -z "$blocker" ]]; then
      per_tool_results+=("{\"slug\":\"$slug\",\"eligible\":true,\"blocker_reason\":null,\"evidence\":$evidence_json}")
      eligible_tools+=("$slug")
    else
      per_tool_results+=("{\"slug\":\"$slug\",\"eligible\":false,\"blocker_reason\":\"$blocker\",\"evidence\":$evidence_json}")
      # Track highest-priority blocker for global_verdict
      # Priority: l2 evidence blockers > l1 floor > l0
      if [[ -z "$global_blocker" ]] || \
         [[ "$blocker" == "blocked_l2_verdict_blocked" ]] || \
         [[ "$blocker" == "blocked_no_l2_regression_evidence" && "$global_blocker" == "blocked_l1_floor" ]] || \
         [[ "$blocker" == "blocked_no_l2_regression_evidence" && "$global_blocker" == "blocked_l0_or_below" ]]; then
        global_blocker="$blocker"
      fi
    fi
  done <<< "$(jq -c '.tools[]' "$CLOSURE_MANIFEST" 2>/dev/null)"

  local total=${#per_tool_results[@]}
  local n_elig=${#eligible_tools[@]}

  if [[ $total -eq 0 ]]; then
    global_verdict="all_eligible"
  elif [[ $n_elig -eq $total ]]; then
    global_verdict="all_eligible"
  elif [[ $n_elig -gt 0 ]]; then
    global_verdict="partial_eligible"
  else
    global_verdict="${global_blocker:-blocked_l1_floor}"
    # Override: if any real L2 recommendation shows blocked, that's the dominant ecosystem verdict
    for l2_scan_rec in "$L2_REG_BASE"/*/recommendation.json; do
      [[ -f "$l2_scan_rec" ]] || continue
      local l2_scan_v; l2_scan_v=$(jq -r '.verdict // ""' "$l2_scan_rec" 2>/dev/null)
      if [[ "$l2_scan_v" == "blocked" ]]; then
        global_verdict="blocked_l2_verdict_blocked"
        break
      fi
    done
  fi

  local per_tool_json="[]"
  [[ ${#per_tool_results[@]} -gt 0 ]] && per_tool_json="[$(IFS=','; echo "${per_tool_results[*]}")]"

  local eligible_json="[]"
  [[ ${#eligible_tools[@]} -gt 0 ]] && eligible_json="$(printf '%s\n' "${eligible_tools[@]}" | jq -R . | jq -s .)"

  local gate_result
  gate_result=$(jq -n \
    --arg sv "v0" \
    --arg ga "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --arg mode "$MODE" \
    --arg gv "$global_verdict" \
    --argjson per_tool "$per_tool_json" \
    --argjson eligible "$eligible_json" \
    --argjson ac "$auto_count" \
    --argjson sm "$([ "$sha_match" = true ] && echo true || echo false)" \
    '{
      schema_version: $sv,
      generated_at: $ga,
      mode: $mode,
      policy_checks_passed: ($gv == "all_eligible" or $gv == "partial_eligible"),
      global_verdict: $gv,
      per_tool: $per_tool,
      eligible_tools: $eligible,
      would_modify_files: [],
      would_create_automation: false,
      invariants: {
        automation_allowed_true_count: $ac,
        closure_manifest_sha256_match: $sm
      }
    }')

  mkdir -p "$OUTPUTS_DIR"
  echo "$gate_result" > "$OUTPUTS_DIR/gate-result.json"

  {
    echo "# Automation Gate v0 — Dry-Run Result"
    echo ""
    echo "**Generated**: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "**Mode**: $MODE"
    echo "**Global Verdict**: \`$global_verdict\`"
    echo "**Would Create Automation**: false"
    echo ""
    echo "## Per-Tool Results"
    echo ""
    echo "| Tool | Eligible | Blocker |"
    echo "|------|----------|---------|"
    for r in "${per_tool_results[@]}"; do
      local s e b
      s=$(echo "$r" | jq -r '.slug' 2>/dev/null)
      e=$(echo "$r" | jq -r '.eligible' 2>/dev/null)
      b=$(echo "$r" | jq -r '.blocker_reason // "—"' 2>/dev/null)
      echo "| $s | $e | $b |"
    done
    echo ""
    echo "## Invariants"
    echo ""
    echo "- automation_allowed=true global count: $auto_count ✓"
    echo "- closure-manifest sha256 match: $sha_match (expected: $EXPECTED_MANIFEST_SHA)"
  } > "$OUTPUTS_DIR/gate-result.md"

  echo "[result] global_verdict=$global_verdict"
  echo "[result] outputs written → $OUTPUTS_DIR/"

  if [[ "$global_verdict" == "all_eligible" || "$global_verdict" == "partial_eligible" ]]; then
    exit 0
  elif [[ "$global_verdict" == "hard_fail_automation_already_true" ]]; then
    exit 3
  else
    exit 1
  fi
}

# ============================================================
# Dispatch
# ============================================================
case "$MODE" in
  self-test)
    run_self_test
    ;;
  dry-run-current | tool)
    run_dry_run_current
    ;;
esac
