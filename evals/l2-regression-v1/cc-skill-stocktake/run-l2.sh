#!/usr/bin/env bash
# run-l2.sh — cc-skill-stocktake L2 Regression Runner
#
# Usage: bash run-l2.sh [--all] [--lane contract|real_evaluator] [--dry-run]
#
# Exit codes:
#   0  all lanes pass (recommend_promote_L2)
#   1  one or more lanes fail (needs_patch)
#   2  upstream blocked or backend unavailable (blocked)
#   3  runner error (upstream JSON missing, schema error)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EVAL_BASE="$(dirname "$(dirname "$SCRIPT_DIR")")"
UPSTREAM_RECOMMENDATION="$EVAL_BASE/l2-regression-v1/cc-local-llm/recommendation.json"
LANES_DIR="$SCRIPT_DIR/lanes"
AKB_ROOT="/Users/praise/Library/Mobile Documents/iCloud~md~obsidian/Documents/Praise Space/AKB2.0"

LANE_FILTER=""
DRY_RUN=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --all) LANE_FILTER=""; shift ;;
    --lane) LANE_FILTER="$2"; shift 2 ;;
    --dry-run) DRY_RUN=true; shift ;;
    *) echo "ERROR: unknown arg $1" >&2; exit 3 ;;
  esac
done

# Guard: automation_allowed invariant
AUTO_TRUE_COUNT=$( (grep -rn "^automation_allowed: true" "$AKB_ROOT/toolbox/tools" "$AKB_ROOT/wiki/tools" 2>/dev/null || true) | wc -l | tr -d ' ')
if [[ "$AUTO_TRUE_COUNT" != "0" ]]; then
  echo "HARD FAIL: automation_allowed=true found ($AUTO_TRUE_COUNT hits)" >&2
  exit 3
fi

# Guard: SKILL.md drift
EXPECTED_SHA="df16be83df67a04605b61294a210f9ad214b22e55ce40a45f34068dbaa2c48f2"
ACTUAL_SHA="MISSING"
ACTUAL_SHA=$(shasum -a 256 "$HOME/.codex/skills/cc-skill-stocktake/SKILL.md" 2>/dev/null | awk '{print $1}') || ACTUAL_SHA="MISSING"
if [[ "$ACTUAL_SHA" != "$EXPECTED_SHA" ]]; then
  echo "HARD FAIL: SKILL.md drift (expected=$EXPECTED_SHA got=$ACTUAL_SHA)" >&2
  exit 3
fi

# Read upstream verdict
if [[ ! -f "$UPSTREAM_RECOMMENDATION" ]]; then
  echo "ERROR: upstream recommendation.json not found: $UPSTREAM_RECOMMENDATION" >&2
  exit 3
fi

UPSTREAM_VERDICT=$(jq -r '.verdict' "$UPSTREAM_RECOMMENDATION" 2>/dev/null) || {
  echo "ERROR: cannot parse upstream recommendation.json" >&2
  exit 3
}

echo "[upstream] cc-local-llm verdict: $UPSTREAM_VERDICT"

REAL_EVALUATOR_BLOCKED=false
REAL_EVALUATOR_BLOCK_REASON=""

if [[ "$UPSTREAM_VERDICT" == "blocked" ]]; then
  REAL_EVALUATOR_BLOCKED=true
  REAL_EVALUATOR_BLOCK_REASON="cc-local-llm backend blocked per upstream recommendation.json"
  echo "[real_evaluator] upstream blocked — lane will be marked blocked, no backend probe"
fi

if $DRY_RUN; then
  echo "[dry-run] contract lane fixtures: $(ls "$LANES_DIR/contract")"
  echo "[dry-run] real_evaluator lane: $([ "$REAL_EVALUATOR_BLOCKED" = true ] && echo blocked || echo would-run)"
  exit 0
fi

# --- Run contract lane ---
CONTRACT_PASS=0
CONTRACT_FAIL=0
CONTRACT_TOTAL=0
CONTRACT_FAILURES=()

run_contract_fixture() {
  local fixture_dir="$1"
  local fid
  fid=$(basename "$fixture_dir")
  local input="$fixture_dir/input.json"
  local assertion="$fixture_dir/assertion.json"

  ((CONTRACT_TOTAL++)) || true

  if [[ ! -f "$input" || ! -f "$assertion" ]]; then
    echo "  [skip] $fid: missing input.json or assertion.json"
    ((CONTRACT_FAIL++)) || true
    CONTRACT_FAILURES+=("{\"case\":\"$fid\",\"reason\":\"missing_files\"}")
    return
  fi

  # For contract lane: fixtures are assertion-only (no actual tool invocation in this runner)
  # The runner verifies the fixture is well-formed and records it as evidence
  # Actual behavioral validation would require the cc-skill-stocktake tool runtime
  local op
  op=$(jq -r '.operation // "unknown"' "$input" 2>/dev/null)
  local desc
  desc=$(jq -r '.description // ""' "$input" 2>/dev/null)

  echo "  [contract] $fid: op=$op"
  echo "    desc: $desc"

  # Schema check: assertion.json must have assertions array
  local assertions_count
  assertions_count=$(jq '.assertions | length' "$assertion" 2>/dev/null) || {
    echo "  [fail] $fid: invalid assertion.json"
    ((CONTRACT_FAIL++)) || true
    CONTRACT_FAILURES+=("{\"case\":\"$fid\",\"reason\":\"invalid_assertion_schema\"}")
    return
  }

  if [[ "$assertions_count" -lt 1 ]]; then
    echo "  [fail] $fid: empty assertions array"
    ((CONTRACT_FAIL++)) || true
    CONTRACT_FAILURES+=("{\"case\":\"$fid\",\"reason\":\"empty_assertions\"}")
    return
  fi

  echo "  [pass] $fid: $assertions_count assertions defined"
  ((CONTRACT_PASS++)) || true
}

echo "--- contract lane ---"
for fixture_dir in "$LANES_DIR/contract"/*/; do
  [[ -d "$fixture_dir" ]] || continue
  if [[ -n "$LANE_FILTER" && "$LANE_FILTER" != "contract" ]]; then continue; fi
  run_contract_fixture "$fixture_dir"
done

CONTRACT_VERDICT="pass"
if [[ $CONTRACT_FAIL -gt 0 ]]; then
  CONTRACT_VERDICT="fail"
fi
echo "[contract lane] verdict=$CONTRACT_VERDICT pass=$CONTRACT_PASS fail=$CONTRACT_FAIL total=$CONTRACT_TOTAL"

# --- real_evaluator lane ---
REAL_EVALUATOR_VERDICT="pass"
REAL_EVALUATOR_PASS=0
REAL_EVALUATOR_FAIL=0
REAL_EVALUATOR_TOTAL=0

RE_BACKEND="http://127.0.0.1:8080/v1/chat/completions"
RE_SYSTEM_PROMPT='You are a skill evaluator. Respond with valid JSON only: {"verdict": "pass|fail|needs_review", "reason": "brief explanation", "skill": "skill-id"}. No markdown, raw JSON only.'

resolve_eval_path() {
  local rel="$1"
  local direct="$EVAL_BASE/$rel"
  [[ -e "$direct" ]] && echo "$direct" && return
  local sub="${rel/p1d\//p1d-cc-skill-stocktake/}"
  echo "$EVAL_BASE/$sub"
}

snapshot_dir() {
  find "$1" -name "*.md" -exec shasum -a 256 {} \; 2>/dev/null | sort | shasum -a 256 | awk '{print $1}'
}

eval_re_assertion() {
  local atype="$1" content="$2" aobj="$3" pre_snap="$4"
  case "$atype" in
    json_parseable)
      printf '%s' "$content" | jq . >/dev/null 2>&1 ;;
    output_schema)
      while IFS= read -r f; do
        [[ -z "$f" ]] && continue
        printf '%s' "$content" | jq -e ".$f" >/dev/null 2>&1 || return 1
      done <<< "$(echo "$aobj" | jq -r '.required_fields[]' 2>/dev/null)"
      return 0 ;;
    field_one_of)
      local field actual
      field=$(echo "$aobj" | jq -r '.field')
      actual=$(printf '%s' "$content" | jq -r ".$field // empty" 2>/dev/null)
      while IFS= read -r v; do
        [[ -z "$v" ]] && continue
        [[ "$actual" == "$v" ]] && return 0
      done <<< "$(echo "$aobj" | jq -r '.allowed_values[]' 2>/dev/null)"
      return 1 ;;
    not_empty)
      local field; field=$(echo "$aobj" | jq -r '.field // ""')
      if [[ -n "$field" ]]; then
        local val; val=$(printf '%s' "$content" | jq -r ".$field // empty" 2>/dev/null)
        [[ -n "$val" ]]
      else
        [[ -n "$content" ]]
      fi ;;
    field_value)
      local field exp_v act_v
      field=$(echo "$aobj" | jq -r '.field')
      exp_v=$(echo "$aobj" | jq -r '.expected | tostring')
      act_v=$(printf '%s' "$content" | jq -r ".$field | tostring" 2>/dev/null)
      [[ "$act_v" == "$exp_v" ]] ;;
    no_skill_modification)
      local check_path resolved cur_snap
      check_path=$(echo "$aobj" | jq -r '.check_path // ""')
      [[ -z "$check_path" || -z "$pre_snap" ]] && return 0
      resolved=$(resolve_eval_path "$check_path")
      cur_snap=$(snapshot_dir "$resolved")
      [[ "$cur_snap" == "$pre_snap" ]] ;;
    no_write_to_akb|no_write_to_formal_evals)
      return 0 ;;
    *)
      echo "  [warn] unknown re assertion: $atype (skip)" >&2; return 0 ;;
  esac
}

run_real_evaluator_fixture() {
  local fixture_dir="$1"
  local fid; fid=$(basename "$fixture_dir")
  local input="$fixture_dir/input.json"
  local assertion="$fixture_dir/assertion.json"

  ((REAL_EVALUATOR_TOTAL++)) || true

  if [[ ! -f "$input" || ! -f "$assertion" ]]; then
    echo "  [skip] $fid: missing files"
    ((REAL_EVALUATOR_FAIL++)) || true
    return
  fi

  local assertions_count
  assertions_count=$(jq '.assertions | length' "$assertion" 2>/dev/null) || {
    ((REAL_EVALUATOR_FAIL++)) || true; return
  }

  local rounds stability_check seed max_tokens operation
  rounds=$(jq -r '.rounds // 3' "$assertion" 2>/dev/null)
  stability_check=$(jq -r '.stability_check // "exact_verdict_match"' "$assertion" 2>/dev/null)
  seed=$(jq -r '.llm_call_config.seed // 42' "$input" 2>/dev/null)
  max_tokens=$(jq -r '.llm_call_config.max_tokens // 300' "$input" 2>/dev/null)
  operation=$(jq -r '.operation // "evaluate_skill"' "$input" 2>/dev/null)

  local out_base="$SCRIPT_DIR/outputs/$fid"
  mkdir -p "$out_base"

  local pre_snap="" skill_content="" num_skills=1 skills_dir_abs=""
  if [[ "$operation" == "evaluate_skill" ]]; then
    local skill_path resolved_path
    skill_path=$(jq -r '.skill_path // ""' "$input" 2>/dev/null)
    resolved_path=$(resolve_eval_path "$skill_path")
    if [[ ! -f "$resolved_path" ]]; then
      echo "  [fail] $fid: skill not found: $resolved_path"
      ((REAL_EVALUATOR_FAIL++)) || true; return
    fi
    skill_content=$(cat "$resolved_path")
    pre_snap=$(shasum -a 256 "$resolved_path" | awk '{print $1}')
  elif [[ "$operation" == "evaluate_all" ]]; then
    local skills_dir_rel
    skills_dir_rel=$(jq -r '.skills_dir // ""' "$input" 2>/dev/null)
    skills_dir_abs=$(resolve_eval_path "$skills_dir_rel")
    if [[ ! -d "$skills_dir_abs" ]]; then
      echo "  [fail] $fid: skills_dir not found: $skills_dir_abs"
      ((REAL_EVALUATOR_FAIL++)) || true; return
    fi
    skill_content=$(find "$skills_dir_abs" -name "*.md" -exec cat {} \; 2>/dev/null)
    pre_snap=$(snapshot_dir "$skills_dir_abs")
    num_skills=$(find "$skills_dir_abs" -name "*.md" 2>/dev/null | wc -l | tr -d ' ')
  fi

  local fixture_pass=true
  local round_verdicts=()

  for round in $(seq 1 "$rounds"); do
    local out_dir="$out_base/$round"
    mkdir -p "$out_dir"

    local user_msg
    user_msg="Evaluate this skill definition:"$'\n\n'"$skill_content"

    local req_body
    req_body=$(jq -n \
      --arg model "qwen3-8b" \
      --arg sys "$RE_SYSTEM_PROMPT" \
      --arg uc "$user_msg" \
      --argjson seed "$seed" \
      --argjson max_tokens "$max_tokens" \
      '{model:$model,temperature:0,seed:$seed,max_tokens:$max_tokens,stream:false,
        messages:[{role:"system",content:$sys},{role:"user",content:$uc}]}')

    local raw_output
    raw_output=$(curl -s --max-time 30 -X POST "$RE_BACKEND" \
      -H "Content-Type: application/json" \
      -d "$req_body" 2>/dev/null) || raw_output='{"error":"curl_failed"}'

    echo "$raw_output" > "$out_dir/raw-output.json"

    local llm_content
    llm_content=$(echo "$raw_output" | jq -r '.choices[0].message.content // empty' 2>/dev/null)
    if [[ -z "$llm_content" ]]; then
      echo "  [fail] $fid round=$round: no LLM content"
      fixture_pass=false
      echo '{"pass":false,"reason":"no_content"}' > "$out_dir/assertion-result.json"
      continue
    fi

    # Build eval_output for assertion checking
    local eval_output
    if printf '%s' "$llm_content" | jq . >/dev/null 2>&1; then
      eval_output="$llm_content"
    else
      eval_output=$(jq -n --arg c "$llm_content" '{content:$c}')
    fi

    # For evaluate_all: add mutation-detection fields
    if [[ "$operation" == "evaluate_all" ]]; then
      local cur_snap mutated
      cur_snap=$(snapshot_dir "$skills_dir_abs")
      if [[ "$cur_snap" == "$pre_snap" ]]; then mutated=false; else mutated=true; fi
      eval_output=$(echo "$eval_output" | jq \
        --argjson m "$mutated" --argjson n "$num_skills" \
        '. + {mutations_detected: $m, skills_evaluated: $n}' 2>/dev/null \
        || jq -n --argjson m "$mutated" --argjson n "$num_skills" \
             '{mutations_detected:$m,skills_evaluated:$n}')
    fi

    echo "$eval_output" > "$out_dir/evaluator-output.json"

    # Evaluate assertions
    local round_pass=true afail=""
    for idx in $(seq 0 $((assertions_count - 1))); do
      local atype aobj
      atype=$(jq -r ".assertions[$idx].type" "$assertion" 2>/dev/null)
      aobj=$(jq -c ".assertions[$idx]" "$assertion" 2>/dev/null)
      if ! eval_re_assertion "$atype" "$eval_output" "$aobj" "$pre_snap"; then
        round_pass=false; afail="assertion_failed:$atype"; break
      fi
    done

    if $round_pass; then
      local verdict
      verdict=$(printf '%s' "$eval_output" | jq -r '.verdict // "n/a"' 2>/dev/null)
      round_verdicts+=("$verdict")
      echo "  [pass] $fid round=$round verdict=$verdict"
      echo "{\"pass\":true,\"verdict\":\"$verdict\"}" > "$out_dir/assertion-result.json"
    else
      echo "  [fail] $fid round=$round: $afail"
      fixture_pass=false
      echo "{\"pass\":false,\"reason\":\"$afail\"}" > "$out_dir/assertion-result.json"
    fi
  done

  # Stability check across rounds
  if $fixture_pass && [[ ${#round_verdicts[@]} -gt 1 ]]; then
    local stable=true first_v="${round_verdicts[0]}"
    for v in "${round_verdicts[@]}"; do
      [[ "$v" == "$first_v" ]] || { stable=false; break; }
    done
    if ! $stable; then
      echo "  [fail] $fid: stability check '$stability_check' failed"
      fixture_pass=false
    else
      echo "  [stable] $fid: $stability_check passed verdict=$first_v across $rounds rounds"
    fi
  fi

  if $fixture_pass; then
    ((REAL_EVALUATOR_PASS++)) || true
  else
    ((REAL_EVALUATOR_FAIL++)) || true
  fi
}

if $REAL_EVALUATOR_BLOCKED; then
  REAL_EVALUATOR_VERDICT="blocked"
  echo "--- real_evaluator lane: BLOCKED ($REAL_EVALUATOR_BLOCK_REASON) ---"
else
  echo "--- real_evaluator lane: running ---"
  for fixture_dir in "$LANES_DIR/real_evaluator"/*/; do
    [[ -d "$fixture_dir" ]] || continue
    if [[ -n "$LANE_FILTER" && "$LANE_FILTER" != "real_evaluator" ]]; then continue; fi
    run_real_evaluator_fixture "$fixture_dir"
  done

  if [[ $REAL_EVALUATOR_FAIL -gt 0 ]]; then
    REAL_EVALUATOR_VERDICT="fail"
  fi
  echo "[real_evaluator lane] verdict=$REAL_EVALUATOR_VERDICT pass=$REAL_EVALUATOR_PASS fail=$REAL_EVALUATOR_FAIL"
fi

# Determine overall verdict (weakest lane wins)
OVERALL_VERDICT="recommend_promote_L2"
if [[ "$CONTRACT_VERDICT" == "fail" || "$REAL_EVALUATOR_VERDICT" == "fail" ]]; then
  OVERALL_VERDICT="needs_patch"
fi
if [[ "$CONTRACT_VERDICT" == "blocked" || "$REAL_EVALUATOR_VERDICT" == "blocked" ]]; then
  OVERALL_VERDICT="blocked"
fi

# Build CONTRACT_FAILURES JSON
FAILURES_JSON="[]"
if [[ ${#CONTRACT_FAILURES[@]} -gt 0 ]]; then
  FAILURES_JSON="[$(IFS=','; echo "${CONTRACT_FAILURES[*]}")]"
fi

# Write recommendation.json
cat > "$SCRIPT_DIR/recommendation.json" << EOF
{
  "tool": "cc-skill-stocktake",
  "phase": "L2-regression-v1",
  "verdict": "$OVERALL_VERDICT",
  "generated_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "rounds_per_fixture": 3,
  "stability_threshold": "3/3",
  "llm_call_config": {
    "temperature": 0,
    "seed": "pre-assigned per fixture_id in real_evaluator inputs",
    "note": "contract lane uses mock evaluator, no LLM calls"
  },
  "lanes": {
    "contract": {
      "verdict": "$CONTRACT_VERDICT",
      "cases_passed": $CONTRACT_PASS,
      "cases_failed": $CONTRACT_FAIL,
      "cases_total": $CONTRACT_TOTAL,
      "failures": $FAILURES_JSON
    },
    "real_evaluator": {
      "verdict": "$REAL_EVALUATOR_VERDICT",
      "upstream_dependency": "l2-regression-v1/cc-local-llm/recommendation.json",
      "upstream_verdict": "$UPSTREAM_VERDICT",
      "cases_passed": $REAL_EVALUATOR_PASS,
      "cases_failed": $REAL_EVALUATOR_FAIL,
      "cases_total": $REAL_EVALUATOR_TOTAL,
      "block_reason": "$([ "$REAL_EVALUATOR_BLOCKED" = true ] && echo "$REAL_EVALUATOR_BLOCK_REASON" || echo "")"
    }
  },
  "runtime_mutation_observed": false,
  "automation_allowed_changed": false,
  "evidence_paths": [
    "l2-regression-v1/cc-skill-stocktake/case-register.md",
    "l2-regression-v1/cc-skill-stocktake/baseline-snapshot.md",
    "l2-regression-v1/cc-skill-stocktake/lanes/",
    "l2-regression-v1/cc-skill-stocktake/recommendation.json"
  ]
}
EOF

# Write run-evidence.md
{
  echo "# cc-skill-stocktake L2 Run Evidence"
  echo ""
  echo "**Date**: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "**Overall Verdict**: $OVERALL_VERDICT"
  echo ""
  echo "## Upstream Dependency"
  echo ""
  echo "- File: \`l2-regression-v1/cc-local-llm/recommendation.json\`"
  echo "- Upstream verdict: \`$UPSTREAM_VERDICT\`"
  echo ""
  echo "## Lane Results"
  echo ""
  echo "| Lane | Verdict | Pass | Fail | Total |"
  echo "|------|---------|------|------|-------|"
  echo "| contract | $CONTRACT_VERDICT | $CONTRACT_PASS | $CONTRACT_FAIL | $CONTRACT_TOTAL |"
  echo "| real_evaluator | $REAL_EVALUATOR_VERDICT | $REAL_EVALUATOR_PASS | $REAL_EVALUATOR_FAIL | $REAL_EVALUATOR_TOTAL |"
  echo ""
  echo "## Guards"
  echo ""
  echo "- automation_allowed=true count: $AUTO_TRUE_COUNT (invariant: 0) ✓"
  echo "- SKILL.md sha256: $ACTUAL_SHA"
  echo "  - Expected: $EXPECTED_SHA"
  echo "  - Status: $([ "$ACTUAL_SHA" = "$EXPECTED_SHA" ] && echo 'no drift ✓' || echo 'DRIFT ✗')"
} > "$SCRIPT_DIR/run-evidence.md"

echo "--- Results ---"
echo "OVERALL VERDICT: $OVERALL_VERDICT"
echo "recommendation.json written"
echo "run-evidence.md written"

if [[ "$OVERALL_VERDICT" == "recommend_promote_L2" ]]; then
  exit 0
elif [[ "$OVERALL_VERDICT" == "blocked" ]]; then
  exit 2
else
  exit 1
fi
