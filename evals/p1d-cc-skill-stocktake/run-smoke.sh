#!/usr/bin/env bash
# run-smoke.sh — P1d C-4 L1 smoke runner for cc-skill-stocktake
#
# 实施 plan v3 §测试计划 §C-4 6 case fixed fixtures
# 全 Claude 自动 (Bash + jq + deterministic mock evaluator);0 LLM call;0 network;0 localhost
# bundled scripts byte-identical 守门;真实 skill 0 触
#
# Usage:
#   bash run-smoke.sh --all          # 跑全 6 case
#   bash run-smoke.sh --case <01-06> # 跑单 case
#   bash run-smoke.sh --preflight    # 仅依赖 + bundled scripts byte-identical 检查

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AKB2_BASE="$(cd "$SCRIPT_DIR/../.." && pwd)"
FIXTURES_DIR="$SCRIPT_DIR/fixtures"
OUTPUTS_DIR="$SCRIPT_DIR/outputs"

BUNDLED="$AKB2_BASE/toolbox/cc-skill-stocktake/references/source-bundled-resources/scripts"
SCAN="$BUNDLED/scan.sh"
QUICKDIFF="$BUNDLED/quick-diff.sh"
SAVE_RESULTS="$BUNDLED/save-results.sh"

# bundled scripts baseline sha256(C-5 hard check #10)
SCAN_BASELINE="b1f16b586e7e2f4c6eeb54ea3895cd5501e64a1e12e242c820193c1193d0b263"
QUICKDIFF_BASELINE="e8b48f77d9e34d0c8043f8fede01387947ecfe0e960f7bd04cce9a44e606c0e8"
SAVE_RESULTS_BASELINE="250968532dc3bf1074f024a710d5d8aeec930e6798269703072f062fad1905bd"

# mktemp wrapper dir(workaround sandbox /var/folders 拒;PATH 前置 wrapper 不改 bundled byte)
MKTEMP_WRAPPER_DIR="$SCRIPT_DIR/.mktemp-wrapper"
WRAP_TMPDIR="/tmp/claude/p1d-smoke"

# ---------- color ----------
if [ -t 1 ]; then
  C_PASS=$'\033[32m'; C_FAIL=$'\033[31m'; C_INFO=$'\033[36m'; C_WARN=$'\033[33m'; C_RESET=$'\033[0m'
else
  C_PASS=""; C_FAIL=""; C_INFO=""; C_WARN=""; C_RESET=""
fi

# ---------- mktemp PATH wrapper(workaround sandbox 拒 /var/folders)----------
setup_mktemp_wrapper() {
  mkdir -p "$WRAP_TMPDIR"
  mkdir -p "$MKTEMP_WRAPPER_DIR"
  cat > "$MKTEMP_WRAPPER_DIR/mktemp" <<EOF
#!/usr/bin/env bash
# mktemp wrapper — force /tmp/claude/p1d-smoke/ as base dir(sandbox-allowed)
# bundled scripts call mktemp -d (no args) and mktemp <template>
WRAP_BASE="$WRAP_TMPDIR"
mkdir -p "\$WRAP_BASE"
if [ \$# -eq 0 ]; then
  /usr/bin/mktemp "\$WRAP_BASE/wrap-file.XXXXXX"
elif [ "\$1" = "-d" ] && [ \$# -eq 1 ]; then
  /usr/bin/mktemp -d "\$WRAP_BASE/wrap-dir.XXXXXX"
else
  # explicit template or other opts — passthrough (covers save-results.sh: mktemp "\$RESULTS.XXXXXX")
  /usr/bin/mktemp "\$@"
fi
EOF
  chmod +x "$MKTEMP_WRAPPER_DIR/mktemp"
  export PATH="$MKTEMP_WRAPPER_DIR:$PATH"
}

# ---------- bundled scripts byte-identical(preflight)----------
verify_bundled_byte_identical() {
  local fail=0
  for pair in "scan.sh:$SCAN_BASELINE" "quick-diff.sh:$QUICKDIFF_BASELINE" "save-results.sh:$SAVE_RESULTS_BASELINE"; do
    name="${pair%%:*}"
    expected="${pair#*:}"
    actual=$(/usr/bin/shasum -a 256 "$BUNDLED/$name" | /usr/bin/awk '{print $1}')
    if [ "$actual" = "$expected" ]; then
      echo "  ${C_PASS}✓${C_RESET} $name byte-identical ($actual)"
    else
      echo "  ${C_FAIL}✗${C_RESET} $name DRIFT (expected=$expected, got=$actual)"
      fail=$((fail + 1))
    fi
  done
  return $fail
}

# ---------- preflight ----------
run_preflight() {
  echo "${C_INFO}===== preflight =====${C_RESET}"
  for cmd in jq bash; do
    if ! command -v $cmd > /dev/null 2>&1; then
      echo "  ${C_FAIL}missing dependency: $cmd${C_RESET}"
      return 1
    fi
  done
  echo "  ${C_PASS}✓${C_RESET} jq + bash 可用"

  # bundled scripts byte-identical
  echo ""
  echo "  --- bundled scripts byte-identical(C-5 hard check #10)---"
  verify_bundled_byte_identical || return 1

  # mktemp wrapper
  echo ""
  echo "  --- mktemp PATH wrapper setup ---"
  setup_mktemp_wrapper
  echo "  ${C_PASS}✓${C_RESET} PATH wrapped: mktemp → $WRAP_TMPDIR"

  # save preflight evidence
  mkdir -p "$OUTPUTS_DIR"
  cat > "$OUTPUTS_DIR/preflight.json" <<EOF
{
  "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "bundled_scripts": {
    "scan.sh": "$SCAN_BASELINE",
    "quick-diff.sh": "$QUICKDIFF_BASELINE",
    "save-results.sh": "$SAVE_RESULTS_BASELINE"
  },
  "bundled_byte_identical": "all 3 ✓",
  "mktemp_wrapper": "$WRAP_TMPDIR",
  "fixture_path": "$FIXTURES_DIR/skills/",
  "fixture_skills": ["skill-a", "skill-b", "skill-c"],
  "deps": "jq + bash + bundled scripts (only-read) + mock evaluator"
}
EOF
  echo "  preflight evidence: $OUTPUTS_DIR/preflight.json"
  return 0
}

# ---------- helpers ----------
write_result() {
  local case_id="$1" status="$2" reason="$3" extra="$4" out="$5"
  cat > "$out" <<EOF
{
  "case_id": "$case_id",
  "status": "$status",
  "reason": "$reason",
  "extra": $extra,
  "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF
}

# ---------- per-case ----------

case_01_scan() {
  local case_id="01-scan"
  local out_dir="$OUTPUTS_DIR/$case_id"
  local raw="$out_dir/raw-output.json"
  local result="$out_dir/assertion-result.json"
  mkdir -p "$out_dir"
  echo ""
  echo "${C_INFO}===== case $case_id =====${C_RESET}"

  SKILL_STOCKTAKE_GLOBAL_DIR="$FIXTURES_DIR/skills" \
  SKILL_STOCKTAKE_PROJECT_DIR="$FIXTURES_DIR/skills" \
  SKILL_STOCKTAKE_OBSERVATIONS="/dev/null" \
    bash "$SCAN" > "$raw" 2> "$out_dir/stderr.txt" || {
      echo "  ${C_FAIL}scan.sh exit non-zero${C_RESET}"
      write_result "$case_id" "fail" "scan_exit_nonzero" '{"stderr_head":""}' "$result"
      return 1
    }

  # JSON parse
  if ! /usr/bin/jq empty "$raw" 2>/dev/null; then
    echo "  ${C_FAIL}scan output not JSON${C_RESET}"
    write_result "$case_id" "fail" "non_json_output" "{}" "$result"
    return 1
  fi

  # 字段 + 真实路径 0 命中 + 含 3 fixture skill
  local pass=true reasons=""
  local skill_count
  skill_count=$(/usr/bin/jq '.skills | length' "$raw")
  if [ "$skill_count" -lt 3 ]; then
    pass=false; reasons="${reasons}skill_count_${skill_count}_lt_3 "
  fi
  for n in skill-a skill-b skill-c; do
    /usr/bin/jq -e ".skills[] | select(.name == \"$n\")" "$raw" > /dev/null 2>&1 || {
      pass=false; reasons="${reasons}missing_$n "
    }
  done
  for field in name description mtime use_7d use_30d path; do
    /usr/bin/jq -e ".skills[0] | has(\"$field\")" "$raw" > /dev/null 2>&1 || {
      pass=false; reasons="${reasons}missing_field_$field "
    }
  done
  # 真实 skill 路径 0 命中(isolation)
  if /usr/bin/grep -q '/Users/praise/.claude/skills' "$raw" || /usr/bin/grep -q '/Users/praise/.codex/skills' "$raw"; then
    pass=false; reasons="${reasons}real_skill_path_in_output "
  fi

  if $pass; then
    echo "  ${C_PASS}PASS${C_RESET} (skill_count=$skill_count, 3 fixture 全在,字段齐,真实路径 0 命中)"
    write_result "$case_id" "pass" "all_assertions_pass" "{\"skill_count\":$skill_count}" "$result"
    return 0
  else
    echo "  ${C_FAIL}FAIL${C_RESET} ($reasons)"
    write_result "$case_id" "fail" "$reasons" "{\"skill_count\":$skill_count}" "$result"
    return 1
  fi
}

case_02_quickdiff_nochange() {
  local case_id="02-quickdiff-nochange"
  local out_dir="$OUTPUTS_DIR/$case_id"
  local raw="$out_dir/raw-output.json"
  local result="$out_dir/assertion-result.json"
  mkdir -p "$out_dir"
  echo ""
  echo "${C_INFO}===== case $case_id =====${C_RESET}"

  SKILL_STOCKTAKE_GLOBAL_DIR="$FIXTURES_DIR/skills" \
  SKILL_STOCKTAKE_PROJECT_DIR="$FIXTURES_DIR/skills" \
  SKILL_STOCKTAKE_OBSERVATIONS="/dev/null" \
    bash "$QUICKDIFF" "$FIXTURES_DIR/baseline-results.json" > "$raw" 2> "$out_dir/stderr.txt" || {
      echo "  ${C_FAIL}quick-diff.sh exit non-zero${C_RESET}"
      write_result "$case_id" "fail" "quickdiff_exit_nonzero" "{}" "$result"
      return 1
    }

  if ! /usr/bin/jq empty "$raw" 2>/dev/null; then
    echo "  ${C_FAIL}output not JSON${C_RESET}"
    write_result "$case_id" "fail" "non_json_output" "{}" "$result"
    return 1
  fi

  local len; len=$(/usr/bin/jq '. | length' "$raw")
  if [ "$len" = "0" ]; then
    echo "  ${C_PASS}PASS${C_RESET} (输出 [],no-change baseline 一致)"
    write_result "$case_id" "pass" "empty_array_as_expected" "{\"length\":0}" "$result"
    return 0
  else
    echo "  ${C_FAIL}FAIL${C_RESET} (期望 [], 实际 length=$len)"
    write_result "$case_id" "fail" "non_empty_length_$len" "{\"length\":$len}" "$result"
    return 1
  fi
}

case_03_quickdiff_changed() {
  local case_id="03-quickdiff-changed"
  local out_dir="$OUTPUTS_DIR/$case_id"
  local raw="$out_dir/raw-output.json"
  local result="$out_dir/assertion-result.json"
  mkdir -p "$out_dir"
  echo ""
  echo "${C_INFO}===== case $case_id =====${C_RESET}"

  SKILL_STOCKTAKE_GLOBAL_DIR="$FIXTURES_DIR/skills" \
  SKILL_STOCKTAKE_PROJECT_DIR="$FIXTURES_DIR/skills" \
  SKILL_STOCKTAKE_OBSERVATIONS="/dev/null" \
    bash "$QUICKDIFF" "$FIXTURES_DIR/changed-results.json" > "$raw" 2> "$out_dir/stderr.txt" || {
      echo "  ${C_FAIL}quick-diff.sh exit non-zero${C_RESET}"
      write_result "$case_id" "fail" "quickdiff_exit_nonzero" "{}" "$result"
      return 1
    }

  if ! /usr/bin/jq empty "$raw" 2>/dev/null; then
    echo "  ${C_FAIL}output not JSON${C_RESET}"
    write_result "$case_id" "fail" "non_json_output" "{}" "$result"
    return 1
  fi

  local len; len=$(/usr/bin/jq '. | length' "$raw")
  local has_skillc; has_skillc=$(/usr/bin/jq '[.[] | select(.path | contains("skill-c")) | select(.is_new == true)] | length' "$raw")

  local pass=true reasons=""
  if [ "$len" = "0" ]; then
    pass=false; reasons="${reasons}empty_array_unexpected "
  fi
  # changed-results 含 a/b 不含 c → c 应被识别为 new
  if [ "$has_skillc" = "0" ]; then
    pass=false; reasons="${reasons}skill_c_not_marked_new "
  fi

  if $pass; then
    echo "  ${C_PASS}PASS${C_RESET} (length=$len, skill-c is_new=true 命中)"
    write_result "$case_id" "pass" "all_assertions_pass" "{\"length\":$len,\"has_skill_c_new\":$has_skillc}" "$result"
    return 0
  else
    echo "  ${C_FAIL}FAIL${C_RESET} ($reasons)"
    write_result "$case_id" "fail" "$reasons" "{\"length\":$len}" "$result"
    return 1
  fi
}

case_04_save_results() {
  local case_id="04-save-results"
  local out_dir="$OUTPUTS_DIR/$case_id"
  local target="$out_dir/results.json"
  local result="$out_dir/assertion-result.json"
  mkdir -p "$out_dir"
  echo ""
  echo "${C_INFO}===== case $case_id =====${C_RESET}"

  # bootstrap: cat baseline-results.json 进 save-results to fixture path(target 不存在则 bootstrap)
  rm -f "$target"
  cat "$FIXTURES_DIR/save-results-input.json" | bash "$SAVE_RESULTS" "$target" 2> "$out_dir/stderr.txt" || {
    echo "  ${C_FAIL}save-results.sh exit non-zero${C_RESET}"
    write_result "$case_id" "fail" "save_results_exit_nonzero" "{}" "$result"
    return 1
  }

  if [ ! -f "$target" ]; then
    echo "  ${C_FAIL}target results.json not created${C_RESET}"
    write_result "$case_id" "fail" "target_not_created" "{}" "$result"
    return 1
  fi

  # ISO UTC + skills 3 项 + 不含真实 cache path
  local pass=true reasons=""
  local evaluated_at; evaluated_at=$(/usr/bin/jq -r '.evaluated_at // ""' "$target")
  if ! echo "$evaluated_at" | /usr/bin/grep -qE '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$'; then
    pass=false; reasons="${reasons}invalid_evaluated_at_'$evaluated_at' "
  fi
  local skill_keys; skill_keys=$(/usr/bin/jq '.skills | keys | length' "$target")
  if [ "$skill_keys" -lt 3 ]; then
    pass=false; reasons="${reasons}skills_count_${skill_keys}_lt_3 "
  fi
  # 真实 cache 路径 0 命中
  if /usr/bin/grep -q '~/.claude/skills/skill-stocktake/results.json' "$target" || \
     [ "$target" = "$HOME/.claude/skills/skill-stocktake/results.json" ]; then
    pass=false; reasons="${reasons}real_cache_path_used "
  fi

  if $pass; then
    echo "  ${C_PASS}PASS${C_RESET} (evaluated_at=$evaluated_at, skills_count=$skill_keys, target=fixture path)"
    write_result "$case_id" "pass" "all_assertions_pass" "{\"evaluated_at\":\"$evaluated_at\",\"skills_count\":$skill_keys}" "$result"
    return 0
  else
    echo "  ${C_FAIL}FAIL${C_RESET} ($reasons)"
    write_result "$case_id" "fail" "$reasons" "{}" "$result"
    return 1
  fi
}

case_05_evaluator() {
  local case_id="05-evaluator"
  local out_dir="$OUTPUTS_DIR/$case_id"
  local raw="$out_dir/raw-output.json"
  local result="$out_dir/assertion-result.json"
  mkdir -p "$out_dir"
  echo ""
  echo "${C_INFO}===== case $case_id =====${C_RESET}"

  bash "$FIXTURES_DIR/evaluator-mock.sh" < "$FIXTURES_DIR/evaluator-input.json" > "$raw" 2> "$out_dir/stderr.txt" || {
    echo "  ${C_FAIL}evaluator-mock.sh exit non-zero${C_RESET}"
    write_result "$case_id" "fail" "evaluator_exit_nonzero" "{}" "$result"
    return 1
  }

  # jq parse + array length 3 + per-element schema + verdict enum + reason 长度 + 含 skill_name + 不含 forbidden + ≥ 2 unique verdict
  local pass=true reasons=""
  /usr/bin/jq -e '. | type == "array"' "$raw" > /dev/null 2>&1 || { pass=false; reasons="${reasons}not_array "; }
  local len; len=$(/usr/bin/jq '. | length' "$raw" 2>/dev/null)
  if [ "$len" != "3" ]; then pass=false; reasons="${reasons}len_${len}_ne_3 "; fi

  # 每对象 schema
  /usr/bin/jq -e 'all(.[]; has("skill_name") and has("verdict") and has("reason"))' "$raw" > /dev/null 2>&1 || {
    pass=false; reasons="${reasons}schema_missing_keys "
  }
  # verdict enum
  /usr/bin/jq -e 'all(.[]; .verdict | test("^(Keep|Improve|Update|Retire|Merge into [a-zA-Z0-9_-]+)$"))' "$raw" > /dev/null 2>&1 || {
    pass=false; reasons="${reasons}verdict_not_in_enum "
  }
  # reason 长度 ≥ 30
  /usr/bin/jq -e 'all(.[]; .reason | length >= 30)' "$raw" > /dev/null 2>&1 || {
    pass=false; reasons="${reasons}reason_too_short "
  }
  # reason 含 skill_name (per-row)
  for i in 0 1 2; do
    local skill_name reason
    skill_name=$(/usr/bin/jq -r ".[$i].skill_name" "$raw")
    reason=$(/usr/bin/jq -r ".[$i].reason" "$raw")
    if ! echo "$reason" | /usr/bin/grep -qF "$skill_name"; then
      pass=false; reasons="${reasons}row_${i}_reason_missing_name "
    fi
  done
  # reason 不含 forbidden token
  if /usr/bin/jq -r '.[].reason' "$raw" 2>/dev/null | /usr/bin/grep -qE 'unchanged|^same$|as before|n/a|no change'; then
    pass=false; reasons="${reasons}forbidden_token_in_reason "
  fi
  # ≥ 2 unique verdict
  local unique; unique=$(/usr/bin/jq '[.[].verdict] | unique | length' "$raw")
  if [ "$unique" -lt 2 ]; then
    pass=false; reasons="${reasons}only_${unique}_unique_verdict "
  fi

  if $pass; then
    echo "  ${C_PASS}PASS${C_RESET} (3 verdict 全 schema 合规, ${unique} unique verdicts)"
    write_result "$case_id" "pass" "all_assertions_pass" "{\"length\":3,\"unique_verdicts\":$unique}" "$result"
    return 0
  else
    echo "  ${C_FAIL}FAIL${C_RESET} ($reasons)"
    write_result "$case_id" "fail" "$reasons" "{\"length\":${len:-0}}" "$result"
    return 1
  fi
}

case_06_isolation_failure() {
  local case_id="06-isolation-failure"
  local out_dir="$OUTPUTS_DIR/$case_id"
  local result="$out_dir/assertion-result.json"
  mkdir -p "$out_dir"
  echo ""
  echo "${C_INFO}===== case $case_id =====${C_RESET}"

  local pass=true reasons=""

  # 6a isolation: 全扫 outputs/ 下所有 JSON + txt evidence(排除 preflight.json + .mktemp-wrapper)
  # 必扫:6 case raw-output.json + 6 case assertion-result.json + 04-save-results/results.json + 6b 4 failure {out,err}.txt
  # 防 case 04 results.json / 6b stderr 类被漏(per Review 2 P2 finding)
  echo "  --- 6a isolation: 真实 skill 路径 0 命中(全扫 outputs/ JSON+TXT,排除 preflight;path 含空格用 -print0/-0)---"
  iso_hit_files=$(/usr/bin/find "$OUTPUTS_DIR" -type f \( -name "*.json" -o -name "*.txt" \) ! -name "preflight.json" -print0 2>/dev/null | \
    /usr/bin/xargs -0 /usr/bin/grep -l -E '/Users/praise/\.claude/skills|/Users/praise/\.codex/skills' 2>/dev/null || true)
  if [ -n "$iso_hit_files" ]; then
    iso_hits=$(printf '%s\n' "$iso_hit_files" | /usr/bin/wc -l | /usr/bin/tr -d '[:space:]')
    pass=false; reasons="${reasons}real_skill_path_in_outputs_${iso_hits} "
    echo "    ${C_FAIL}isolation hits: $iso_hits${C_RESET}"
    printf '%s\n' "$iso_hit_files" | /usr/bin/sed 's|^|      hit: |'
  else
    echo "    ${C_PASS}isolation hits: 0 ✓${C_RESET}"
  fi

  # 6a isolation: 真实 SKILL.md sha256 不变(bash 3.2 兼容,用 plain pairs)
  echo "  --- 6a isolation: 5 真实 SKILL.md sha256 不变(本 P1d 只改 cc-skill-stocktake/SKILL.md anchor)---"
  for pair in \
    "cc-skill-stocktake:df16be83df67a04605b61294a210f9ad214b22e55ce40a45f34068dbaa2c48f2" \
    "cc-executable-plan:5af73fdadd91097cc60f8e6c3622427aeaac8382bda0d1525debe564f2c46d0a" \
    "cc-review:592cf3ed4b2c6e3f96c63a500bdbe3bebbc375a19983debbfa7f1877f406d274" \
    "cc-akb-search:b669380de8a0cd4319f0c978f4cfa69d2757b004a8569fc3adac8f633637f982" \
    "cc-local-llm:3a31ebe4f5938864e752ea51d35d89403e62d31a352ac3eaf01a784721c73050"; do
    slug="${pair%%:*}"
    expected_sha="${pair#*:}"
    actual=$(/usr/bin/shasum -a 256 "$HOME/.codex/skills/$slug/SKILL.md" | /usr/bin/awk '{print $1}')
    if [ "$actual" != "$expected_sha" ]; then
      pass=false; reasons="${reasons}skill_${slug}_sha_drift "
    fi
  done

  # 6a isolation: bundled scripts byte-identical
  echo "  --- 6a isolation: bundled scripts byte-identical(C-5 hard check #10)---"
  for pair in "scan.sh:$SCAN_BASELINE" "quick-diff.sh:$QUICKDIFF_BASELINE" "save-results.sh:$SAVE_RESULTS_BASELINE"; do
    name="${pair%%:*}"
    expected="${pair#*:}"
    actual=$(/usr/bin/shasum -a 256 "$BUNDLED/$name" | /usr/bin/awk '{print $1}')
    if [ "$actual" != "$expected" ]; then
      pass=false; reasons="${reasons}bundled_${name}_drift "
    fi
  done

  # 6a isolation: 真实 results.json sha256 不变(若存在)
  REAL_CACHE="$HOME/.claude/skills/skill-stocktake/results.json"
  if [ -f "$REAL_CACHE" ]; then
    echo "  --- 6a isolation: 真实 results.json 本 P1d 全程未写(file 存在,记录 sha)---"
    /usr/bin/shasum -a 256 "$REAL_CACHE" | /usr/bin/awk '{print "    real cache sha: " substr($1,1,12)}'
  else
    echo "  --- 6a isolation: 真实 results.json 不存在(0 风险)---"
  fi

  # 6b failure injection
  echo "  --- 6b.1 failure: quick-diff invalid evaluated_at ---"
  bash "$QUICKDIFF" "$FIXTURES_DIR/invalid-results.json" > "$out_dir/6b1-out.txt" 2> "$out_dir/6b1-err.txt"
  ec=$?
  if [ $ec -eq 0 ]; then
    pass=false; reasons="${reasons}6b1_quickdiff_invalid_should_fail "
    echo "    ${C_FAIL}fail${C_RESET}: exit=$ec (应非 0)"
  else
    echo "    ${C_PASS}pass${C_RESET}: quick-diff 拒非法 evaluated_at (exit=$ec)"
  fi

  echo "  --- 6b.2 failure: save-results stdin 非 JSON ---"
  local target_6b2="$out_dir/6b2-target.json"
  echo "fixture-non-json-baseline" > "$target_6b2.before"
  rm -f "$target_6b2"
  echo "not json content" | bash "$SAVE_RESULTS" "$target_6b2" > "$out_dir/6b2-out.txt" 2> "$out_dir/6b2-err.txt"
  ec=$?
  if [ $ec -eq 0 ] || [ -f "$target_6b2" ]; then
    pass=false; reasons="${reasons}6b2_save_results_should_fail "
    echo "    ${C_FAIL}fail${C_RESET}: exit=$ec (应非 0) 或 target 被破坏"
  else
    echo "    ${C_PASS}pass${C_RESET}: save-results 拒非 JSON stdin (exit=$ec, 不写 target)"
  fi

  echo "  --- 6b.3 failure: evaluator-mock stdin 非 JSON ---"
  echo "not json" | bash "$FIXTURES_DIR/evaluator-mock.sh" > "$out_dir/6b3-out.txt" 2> "$out_dir/6b3-err.txt"
  ec=$?
  if [ $ec -eq 0 ]; then
    pass=false; reasons="${reasons}6b3_evaluator_should_fail "
    echo "    ${C_FAIL}fail${C_RESET}: exit=$ec (应非 0)"
  else
    echo "    ${C_PASS}pass${C_RESET}: mock evaluator 拒非 JSON stdin (exit=$ec)"
  fi

  echo "  --- 6b.4 failure: evaluator-mock unknown skill ---"
  echo '{"skills":[{"name":"skill-unknown"}]}' | bash "$FIXTURES_DIR/evaluator-mock.sh" > "$out_dir/6b4-out.txt" 2> "$out_dir/6b4-err.txt"
  ec=$?
  if [ $ec -eq 0 ]; then
    pass=false; reasons="${reasons}6b4_evaluator_unknown_should_fail "
    echo "    ${C_FAIL}fail${C_RESET}: exit=$ec (应非 0)"
  else
    echo "    ${C_PASS}pass${C_RESET}: mock evaluator 拒未知 skill (exit=$ec)"
  fi

  if $pass; then
    echo "  ${C_PASS}PASS${C_RESET} (6a isolation 全过 + 6b 4 failure injection 全 hard fail)"
    write_result "$case_id" "pass" "all_isolation_and_failure_assertions_pass" "{\"isolation\":\"ok\",\"failure\":\"4/4_hard_fail\"}" "$result"
    return 0
  else
    echo "  ${C_FAIL}FAIL${C_RESET} ($reasons)"
    write_result "$case_id" "fail" "$reasons" "{}" "$result"
    return 1
  fi
}

# ---------- main ----------
main() {
  if [ $# -eq 0 ]; then
    cat <<USAGE
Usage:
  bash run-smoke.sh --all
  bash run-smoke.sh --case <01|02|03|04|05|06>
  bash run-smoke.sh --preflight

Fixtures: $FIXTURES_DIR
Outputs:  $OUTPUTS_DIR
Bundled (only-read): $BUNDLED
USAGE
    exit 2
  fi

  case "$1" in
    --preflight)
      run_preflight || exit 1
      exit 0
      ;;
    --case)
      [ -n "${2:-}" ] || { echo "missing case id"; exit 2; }
      run_preflight || exit 1
      case "$2" in
        01) case_01_scan; exit $? ;;
        02) case_02_quickdiff_nochange; exit $? ;;
        03) case_03_quickdiff_changed; exit $? ;;
        04) case_04_save_results; exit $? ;;
        05) case_05_evaluator; exit $? ;;
        06) case_06_isolation_failure; exit $? ;;
        *) echo "unknown case $2"; exit 2 ;;
      esac
      ;;
    --all)
      run_preflight || { echo "${C_FAIL}preflight 失败,abort${C_RESET}"; exit 1; }
      local pass_count=0 fail_count=0
      for case_fn in case_01_scan case_02_quickdiff_nochange case_03_quickdiff_changed case_04_save_results case_05_evaluator case_06_isolation_failure; do
        if $case_fn; then
          pass_count=$((pass_count + 1))
        else
          fail_count=$((fail_count + 1))
        fi
      done
      echo ""
      echo "${C_INFO}===== summary =====${C_RESET}"
      echo "  pass: $pass_count / 6"
      echo "  fail: $fail_count / 6"
      if [ $fail_count -eq 0 ]; then
        echo "  ${C_PASS}6/6 PASS — proceed to Review 2${C_RESET}"
        exit 0
      else
        echo "  ${C_FAIL}$fail_count case(s) FAILED — verdict per plan v3 §verdict anchor${C_RESET}"
        exit 1
      fi
      ;;
    *)
      echo "unknown arg: $1"
      exit 2
      ;;
  esac
}

main "$@"
