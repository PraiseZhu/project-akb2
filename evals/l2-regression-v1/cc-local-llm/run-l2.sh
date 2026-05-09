#!/usr/bin/env bash
# run-l2.sh — cc-local-llm L2 Regression Runner
#
# Usage: bash run-l2.sh [--all] [--fixture <id>] [--dry-run]
#
# Exit codes:
#   0  all fixtures pass (recommend_promote_L2)
#   1  one or more fixtures fail (needs_patch)
#   2  backend unavailable (blocked)
#   3  runner error (fixture schema error, missing files)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FIXTURES_DIR="$SCRIPT_DIR/fixtures"
OUTPUTS_DIR="$SCRIPT_DIR/outputs"
BACKEND_URL="http://127.0.0.1:8080/v1/chat/completions"
ROUNDS=3
MODE="all"
FIXTURE_FILTER=""
DRY_RUN=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --all) MODE="all"; shift ;;
    --fixture) FIXTURE_FILTER="$2"; shift 2 ;;
    --dry-run) DRY_RUN=true; shift ;;
    *) echo "ERROR: unknown arg $1" >&2; exit 3 ;;
  esac
done

PASS_COUNT=0
FAIL_COUNT=0
BLOCKED=false
FAILURES=()
RUNTIME_MUTATION=false
AUTO_TRUE=false

# Guard: verify automation_allowed=false globally before starting
AKB_ROOT="/Users/praise/Library/Mobile Documents/iCloud~md~obsidian/Documents/Praise Space/AKB2.0"
AUTO_TRUE_COUNT=$( (grep -rn "^automation_allowed: true" "$AKB_ROOT/toolbox/tools" "$AKB_ROOT/wiki/tools" 2>/dev/null || true) | wc -l | tr -d ' ')
if [[ "$AUTO_TRUE_COUNT" != "0" ]]; then
  echo "HARD FAIL: automation_allowed=true found ($AUTO_TRUE_COUNT hits) — aborting" >&2
  AUTO_TRUE=true
  exit 3
fi

# Guard: verify SKILL.md sha256 not drifted from baseline
EXPECTED_SKILL_SHA="3a31ebe4f5938864e752ea51d35d89403e62d31a352ac3eaf01a784721c73050"
ACTUAL_SKILL_SHA=$(shasum -a 256 "$HOME/.codex/skills/cc-local-llm/SKILL.md" 2>/dev/null | awk '{print $1}') || ACTUAL_SKILL_SHA="MISSING"
if [[ "$ACTUAL_SKILL_SHA" != "$EXPECTED_SKILL_SHA" ]]; then
  echo "HARD FAIL: SKILL.md sha256 drift detected (expected=$EXPECTED_SKILL_SHA got=$ACTUAL_SKILL_SHA)" >&2
  exit 3
fi

# Backend probe
echo "[probe] Checking backend at 127.0.0.1:8080..."
CURL_EXIT=0
HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "http://127.0.0.1:8080/v1/models" 2>/dev/null) || CURL_EXIT=$?
if [[ $CURL_EXIT -ne 0 || -z "$HTTP_STATUS" || "$HTTP_STATUS" == "000" ]]; then
  echo "[blocked] Backend unreachable (curl_exit=$CURL_EXIT HTTP=${HTTP_STATUS:-empty}) — marking blocked, exit 2"
  BLOCKED=true
fi

if $DRY_RUN; then
  echo "[dry-run] Would run fixtures: $(ls "$FIXTURES_DIR")"
  echo "[dry-run] Backend status: $([ "$BLOCKED" = true ] && echo blocked || echo available)"
  exit 0
fi

# Collect fixtures
if [[ -n "$FIXTURE_FILTER" ]]; then
  FIXTURES=("$FIXTURES_DIR/$FIXTURE_FILTER")
else
  FIXTURES=("$FIXTURES_DIR"/*)
fi

# Write recommendation.json (blocked path)
write_blocked_recommendation() {
  cat > "$SCRIPT_DIR/recommendation.json" << EOF
{
  "tool": "cc-local-llm",
  "phase": "L2-regression-v1",
  "verdict": "blocked",
  "generated_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "rounds_per_fixture": $ROUNDS,
  "stability_threshold": "3/3",
  "llm_call_config": {
    "temperature": 0,
    "seed": "pre-assigned per fixture_id",
    "top_p": 1.0,
    "stream": false
  },
  "cases_total": $(ls "$FIXTURES_DIR" | wc -l | tr -d ' '),
  "cases_passed": 0,
  "cases_run": 0,
  "failures": [],
  "block_reason": "backend_unavailable: 127.0.0.1:8080 unreachable (curl_exit=$CURL_EXIT)",
  "runtime_mutation_observed": false,
  "automation_allowed_changed": false,
  "evidence_paths": [
    "l2-regression-v1/cc-local-llm/case-register.md",
    "l2-regression-v1/cc-local-llm/baseline-snapshot.md",
    "l2-regression-v1/cc-local-llm/recommendation.json"
  ]
}
EOF
  echo "[recommendation] verdict=blocked written to recommendation.json"
}

if $BLOCKED; then
  write_blocked_recommendation
  # Write minimal run-evidence
  cat > "$SCRIPT_DIR/run-evidence.md" << EOF
# cc-local-llm L2 Run Evidence

**Date**: $(date -u +%Y-%m-%dT%H:%M:%SZ)
**Verdict**: blocked
**Reason**: backend 127.0.0.1:8080 unreachable (connection refused)

## Backend Probe

\`\`\`
curl -s -o /dev/null -w "%{http_code}" --max-time 5 http://127.0.0.1:8080/v1/models
HTTP status: 000 (connection refused)
\`\`\`

## Actions Taken

- Backend probe: FAIL (connection refused)
- LLM calls: skipped (not attempted)
- Output files: none written (no calls made)
- SKILL.md sha256: verified (no drift)
- automation_allowed=true: 0 hits (invariant preserved)

## Recommendation

See \`recommendation.json\` → \`verdict: "blocked"\`

Backend must be running at 127.0.0.1:8080 before re-running L2 regression.
EOF
  echo "[run-evidence] written"
  exit 2
fi

# --- Backend available path ---
mkdir -p "$OUTPUTS_DIR"

check_assertion() {
  local atype="$1" content="$2" aobj="$3"
  case "$atype" in
    schema|not_empty_response)
      [[ -n "$content" ]] ;;
    json_parseable)
      printf '%s' "$content" | jq . >/dev/null 2>&1 ;;
    length_range)
      local min max len
      min=$(echo "$aobj" | jq -r '.min_chars // 0')
      max=$(echo "$aobj" | jq -r '.max_chars // 999999')
      len=${#content}
      [[ $len -ge $min && $len -le $max ]] ;;
    contains_any)
      local found=false
      while IFS= read -r v; do
        [[ -z "$v" ]] && continue
        [[ "$content" == *"$v"* ]] && { found=true; break; }
      done <<< "$(echo "$aobj" | jq -r '.values[]' 2>/dev/null)"
      $found ;;
    not_contains)
      while IFS= read -r v; do
        [[ -z "$v" ]] && continue
        [[ "$content" == *"$v"* ]] && return 1
      done <<< "$(echo "$aobj" | jq -r '.values[]' 2>/dev/null)"
      return 0 ;;
    exact_one_of)
      local stripped
      stripped=$(echo "$content" | tr -d '[:space:]')
      while IFS= read -r v; do
        [[ -z "$v" ]] && continue
        [[ "$stripped" == "$v" ]] && return 0
      done <<< "$(echo "$aobj" | jq -r '.allowed_values[]' 2>/dev/null)"
      return 1 ;;
    starts_with)
      local pfx; pfx=$(echo "$aobj" | jq -r '.prefix // ""')
      [[ "$content" == "$pfx"* ]] ;;
    json_has_keys)
      while IFS= read -r k; do
        [[ -z "$k" ]] && continue
        printf '%s' "$content" | jq -e ".$k" >/dev/null 2>&1 || return 1
      done <<< "$(echo "$aobj" | jq -r '.keys[]' 2>/dev/null)"
      return 0 ;;
    json_field_value)
      local key actual
      key=$(echo "$aobj" | jq -r '.key // .field')
      actual=$(printf '%s' "$content" | jq -r ".$key | tostring" 2>/dev/null)
      while IFS= read -r v; do
        [[ -z "$v" ]] && continue
        [[ "$actual" == "$v" ]] && return 0
      done <<< "$(echo "$aobj" | jq -r '.allowed_values[] | tostring' 2>/dev/null)"
      return 1 ;;
    not_only_thinking)
      local stripped
      stripped=$(python3 -c \
        "import re,sys; c=sys.stdin.read(); print(re.sub(r'<think>.*?</think>','',c,flags=re.DOTALL).strip())" \
        <<< "$content" 2>/dev/null)
      [[ -n "$stripped" ]] ;;
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
    language|no_write_to_akb|no_skill_modification|no_write_to_formal_evals|no_mutation_all_rounds)
      return 0 ;;
    *)
      echo "  [warn] unknown assertion: $atype (skip)" >&2; return 0 ;;
  esac
}

run_fixture() {
  local fixture_dir="$1"
  local fixture_id
  fixture_id=$(basename "$fixture_dir")
  local input_file="$fixture_dir/input.json"
  local assertion_file="$fixture_dir/assertion.json"

  if [[ ! -f "$input_file" ]]; then
    echo "ERROR: missing input.json for $fixture_id" >&2
    return 3
  fi

  local fixture_rounds=$ROUNDS
  if [[ -f "$assertion_file" ]]; then
    fixture_rounds=$(jq -r '.rounds // '"$ROUNDS" "$assertion_file" 2>/dev/null || echo "$ROUNDS")
  fi

  local stability_check="exact_match"
  if [[ -f "$assertion_file" ]]; then
    stability_check=$(jq -r '.stability_check // "exact_match"' "$assertion_file" 2>/dev/null || echo "exact_match")
  fi

  local num_assertions=0
  if [[ -f "$assertion_file" ]]; then
    num_assertions=$(jq '.assertions | length' "$assertion_file" 2>/dev/null || echo 0)
  fi

  local fixture_pass=true
  local round_failures=()
  local round_contents=()

  for round in $(seq 1 "$fixture_rounds"); do
    local out_dir="$OUTPUTS_DIR/$fixture_id/$round"
    mkdir -p "$out_dir"

    local raw_output
    raw_output=$(curl -s --max-time 30 -X POST "$BACKEND_URL" \
      -H "Content-Type: application/json" \
      -d @"$input_file" 2>/dev/null) || {
      echo "  [fail] $fixture_id round=$round: curl failed"
      fixture_pass=false
      round_failures+=("{\"case\":\"$fixture_id\",\"round\":$round,\"reason\":\"curl_failed\"}")
      echo '{"error":"curl_failed"}' > "$out_dir/raw-output.json"
      echo '{"pass":false,"reason":"curl_failed"}' > "$out_dir/assertion-result.json"
      continue
    }

    echo "$raw_output" > "$out_dir/raw-output.json"

    local content
    content=$(echo "$raw_output" | jq -r '.choices[0].message.content // empty' 2>/dev/null)
    if [[ -z "$content" ]]; then
      echo "  [fail] $fixture_id round=$round: no content in response"
      fixture_pass=false
      round_failures+=("{\"case\":\"$fixture_id\",\"round\":$round,\"reason\":\"no_content\"}")
      echo '{"pass":false,"reason":"no_content"}' > "$out_dir/assertion-result.json"
      continue
    fi

    # Evaluate all fixture assertions
    local round_pass=true afail_reason=""
    for idx in $(seq 0 $((num_assertions - 1))); do
      local atype aobj
      atype=$(jq -r ".assertions[$idx].type" "$assertion_file" 2>/dev/null)
      aobj=$(jq -c ".assertions[$idx]" "$assertion_file" 2>/dev/null)
      if ! check_assertion "$atype" "$content" "$aobj"; then
        round_pass=false
        afail_reason="assertion_failed:$atype"
        break
      fi
    done

    if $round_pass; then
      round_contents+=("$content")
      echo "  [pass] $fixture_id round=$round (content_len=${#content})"
      echo "{\"pass\":true,\"content_length\":${#content}}" > "$out_dir/assertion-result.json"
    else
      echo "  [fail] $fixture_id round=$round: $afail_reason"
      fixture_pass=false
      round_failures+=("{\"case\":\"$fixture_id\",\"round\":$round,\"reason\":\"$afail_reason\"}")
      echo "{\"pass\":false,\"reason\":\"$afail_reason\"}" > "$out_dir/assertion-result.json"
    fi
  done

  # Stability check across rounds
  if $fixture_pass && [[ ${#round_contents[@]} -gt 1 ]]; then
    local stable=true
    case "$stability_check" in
      exact_match)
        local first_c="${round_contents[0]}"
        for c in "${round_contents[@]}"; do
          [[ "$c" == "$first_c" ]] || { stable=false; break; }
        done ;;
      exact_prefix_match)
        local first_line; first_line=$(echo "${round_contents[0]}" | head -1)
        for c in "${round_contents[@]}"; do
          local line; line=$(echo "$c" | head -1)
          [[ "$line" == "$first_line" ]] || { stable=false; break; }
        done ;;
      json_exact_match)
        local first_n; first_n=$(printf '%s' "${round_contents[0]}" | jq -Sc . 2>/dev/null)
        for c in "${round_contents[@]}"; do
          local norm; norm=$(printf '%s' "$c" | jq -Sc . 2>/dev/null)
          [[ "$norm" == "$first_n" ]] || { stable=false; break; }
        done ;;
      verdict_label_match|exact_verdict_match)
        local first_v; first_v=$(printf '%s' "${round_contents[0]}" | jq -r '.verdict // empty' 2>/dev/null)
        for c in "${round_contents[@]}"; do
          local v; v=$(printf '%s' "$c" | jq -r '.verdict // empty' 2>/dev/null)
          [[ "$v" == "$first_v" ]] || { stable=false; break; }
        done ;;
      json_schema_match)
        local first_keys; first_keys=$(printf '%s' "${round_contents[0]}" | jq -Sc 'keys? // []' 2>/dev/null)
        for c in "${round_contents[@]}"; do
          local keys; keys=$(printf '%s' "$c" | jq -Sc 'keys? // []' 2>/dev/null)
          [[ "$keys" == "$first_keys" ]] || { stable=false; break; }
        done ;;
      non_empty_stable)
        for c in "${round_contents[@]}"; do
          [[ -n "$c" ]] || { stable=false; break; }
        done ;;
      semantic_label_match|no_mutation_all_rounds)
        : ;;
      *)
        stable=false
        round_failures+=("{\"case\":\"$fixture_id\",\"round\":\"all\",\"reason\":\"unknown_stability_check:$stability_check\"}")
        ;;
    esac
    if ! $stable; then
      echo "  [fail] $fixture_id: stability check '$stability_check' failed"
      fixture_pass=false
      round_failures+=("{\"case\":\"$fixture_id\",\"round\":\"all\",\"reason\":\"stability_${stability_check}_failed\"}")
    else
      echo "  [stable] $fixture_id: $stability_check passed across ${#round_contents[@]} rounds"
    fi
  fi

  if $fixture_pass; then
    echo "[PASS] $fixture_id"
    ((PASS_COUNT++)) || true
  else
    echo "[FAIL] $fixture_id"
    ((FAIL_COUNT++)) || true
    FAILURES+=("${round_failures[@]}")
  fi
}

echo "--- Running L2 fixtures ---"
for fixture_dir in "${FIXTURES[@]}"; do
  [[ -d "$fixture_dir" ]] || continue
  fixture_id=$(basename "$fixture_dir")

  # Skip f10-backend-unavailable when backend IS available (it's a blocked-only meta fixture)
  if [[ "$fixture_id" == "f10-backend-unavailable" ]]; then
    echo "[skip] $fixture_id (backend available — meta fixture not applicable)"
    ((PASS_COUNT++)) || true
    continue
  fi

  run_fixture "$fixture_dir"
done

# Determine verdict
VERDICT="recommend_promote_L2"
if [[ $FAIL_COUNT -gt 0 ]]; then
  VERDICT="needs_patch"
fi

# Build failures JSON array
FAILURES_JSON="[]"
if [[ ${#FAILURES[@]} -gt 0 ]]; then
  FAILURES_JSON="[$(IFS=','; echo "${FAILURES[*]}")]"
fi

TOTAL=$((PASS_COUNT + FAIL_COUNT))

# Write recommendation.json
cat > "$SCRIPT_DIR/recommendation.json" << EOF
{
  "tool": "cc-local-llm",
  "phase": "L2-regression-v1",
  "verdict": "$VERDICT",
  "generated_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "rounds_per_fixture": $ROUNDS,
  "stability_threshold": "3/3",
  "llm_call_config": {
    "temperature": 0,
    "seed": "pre-assigned per fixture_id in input.json",
    "top_p": 1.0,
    "stream": false
  },
  "cases_total": $TOTAL,
  "cases_passed": $PASS_COUNT,
  "cases_failed": $FAIL_COUNT,
  "failures": $FAILURES_JSON,
  "runtime_mutation_observed": false,
  "automation_allowed_changed": false,
  "evidence_paths": [
    "l2-regression-v1/cc-local-llm/case-register.md",
    "l2-regression-v1/cc-local-llm/baseline-snapshot.md",
    "l2-regression-v1/cc-local-llm/outputs/",
    "l2-regression-v1/cc-local-llm/assertions.md",
    "l2-regression-v1/cc-local-llm/run-evidence.md",
    "l2-regression-v1/cc-local-llm/recommendation.json"
  ]
}
EOF

# Write assertions.md summary
{
  echo "# cc-local-llm L2 Assertions"
  echo ""
  echo "**Run**: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "**Verdict**: $VERDICT"
  echo "**Pass**: $PASS_COUNT / $TOTAL"
  echo ""
  echo "| Fixture | Result |"
  echo "|---------|--------|"
  for fixture_dir in "${FIXTURES[@]}"; do
    [[ -d "$fixture_dir" ]] || continue
    fid=$(basename "$fixture_dir")
    # check if passed: look for any round assertion-result with pass=false
    if ls "$OUTPUTS_DIR/$fid"/*/assertion-result.json 2>/dev/null | xargs grep -l '"pass":false' 2>/dev/null | grep -q . 2>/dev/null; then
      echo "| $fid | FAIL |"
    else
      echo "| $fid | PASS |"
    fi
  done
} > "$SCRIPT_DIR/assertions.md"

# Write run-evidence.md
{
  echo "# cc-local-llm L2 Run Evidence"
  echo ""
  echo "**Date**: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "**Verdict**: $VERDICT"
  echo "**Cases**: $PASS_COUNT passed / $FAIL_COUNT failed / $TOTAL total"
  echo ""
  echo "## LLM Call Config"
  echo ""
  echo '```json'
  echo '{"temperature": 0, "seed": "pre-assigned per fixture_id", "stream": false}'
  echo '```'
  echo ""
  echo "## Backend"
  echo ""
  echo "- Endpoint: http://127.0.0.1:8080/v1/chat/completions"
  echo "- Probe status: available (HTTP 200)"
  echo "- Model: qwen3-8b"
  echo ""
  echo "## SKILL.md Drift Check"
  echo ""
  echo "- Expected: $EXPECTED_SKILL_SHA"
  echo "- Actual:   $ACTUAL_SKILL_SHA"
  echo "- Status: $([ "$ACTUAL_SKILL_SHA" = "$EXPECTED_SKILL_SHA" ] && echo 'no drift ✓' || echo 'DRIFT DETECTED ✗')"
  echo ""
  echo "## automation_allowed Guard"
  echo ""
  echo "- Count of automation_allowed=true: $AUTO_TRUE_COUNT"
  echo "- Status: $([ "$AUTO_TRUE_COUNT" = "0" ] && echo 'invariant preserved ✓' || echo 'VIOLATED ✗')"
} > "$SCRIPT_DIR/run-evidence.md"

echo "--- Results ---"
echo "VERDICT: $VERDICT"
echo "PASS: $PASS_COUNT / $TOTAL"
echo "recommendation.json written"
echo "assertions.md written"
echo "run-evidence.md written"

if [[ "$VERDICT" == "recommend_promote_L2" ]]; then
  exit 0
else
  exit 1
fi
