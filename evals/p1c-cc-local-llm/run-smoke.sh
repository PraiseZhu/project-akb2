#!/usr/bin/env bash
# run-smoke.sh — P1c C-4 L1 smoke runner for cc-local-llm
#
# 实施 plan v6 §测试计划 §C-4 fixed fixtures 的 5 case 语义断言 smoke
# 因 Claude OS-level network sandbox 阻 127.0.0.1, 本 runner 由 user 在本机跑
#
# Usage:
#   bash run-smoke.sh --all          # 跑全 5 case
#   bash run-smoke.sh --case 01      # 跑单 case (01-05)
#   bash run-smoke.sh --preflight    # 仅跑 health + models 检查, 不打 chat

set -uo pipefail

BACKEND_BASE="${BACKEND_BASE:-http://127.0.0.1:8080}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FIXTURES_DIR="$SCRIPT_DIR/fixtures"
OUTPUTS_DIR="$SCRIPT_DIR/outputs"

# ---------- color ----------
if [ -t 1 ]; then
  C_PASS=$'\033[32m'; C_FAIL=$'\033[31m'; C_INFO=$'\033[36m'; C_WARN=$'\033[33m'; C_RESET=$'\033[0m'
else
  C_PASS=""; C_FAIL=""; C_INFO=""; C_WARN=""; C_RESET=""
fi

# ---------- preflight ----------
run_preflight() {
  echo "${C_INFO}===== preflight: $BACKEND_BASE =====${C_RESET}"

  # /health
  local health_code health_body
  health_code=$(curl -sf -m 5 -o /dev/null -w "%{http_code}" "$BACKEND_BASE/health" 2>/dev/null || echo "000")
  health_body=$(curl -sf -m 5 "$BACKEND_BASE/health" 2>/dev/null || echo "")
  if [ "$health_code" = "200" ] && [ "$health_body" = "OK" ]; then
    echo "  /health: ${C_PASS}OK${C_RESET} (HTTP 200, body=OK)"
  else
    echo "  /health: ${C_FAIL}FAIL${C_RESET} (HTTP $health_code, body='$health_body')"
    return 1
  fi

  # /v1/models
  local models_json
  models_json=$(curl -sf -m 5 "$BACKEND_BASE/v1/models" 2>/dev/null || echo "")
  if [ -z "$models_json" ]; then
    echo "  /v1/models: ${C_FAIL}FAIL${C_RESET} (empty response)"
    return 1
  fi
  if ! echo "$models_json" | jq -e '.data | type == "array"' > /dev/null 2>&1; then
    echo "  /v1/models: ${C_FAIL}FAIL${C_RESET} (not OpenAI-compatible JSON)"
    return 1
  fi
  echo "  /v1/models: ${C_PASS}OK${C_RESET} (data[] count=$(echo "$models_json" | jq '.data | length'))"

  # chat fallback check
  local chat_available=""
  for m in qwen3-8b qwen2.5-14b qwen3-32b; do
    if echo "$models_json" | jq -e ".data[] | select(.id == \"$m\")" > /dev/null 2>&1; then
      chat_available="$chat_available $m"
    fi
  done
  if [ -z "$chat_available" ]; then
    echo "  chat fallback: ${C_FAIL}FAIL${C_RESET} (qwen3-8b / qwen2.5-14b / qwen3-32b 全 missing — hard fail)"
    return 1
  fi
  echo "  chat fallback: ${C_PASS}OK${C_RESET} (available:$chat_available)"

  # save preflight evidence
  mkdir -p "$OUTPUTS_DIR"
  cat > "$OUTPUTS_DIR/preflight.json" <<EOF
{
  "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "backend_base": "$BACKEND_BASE",
  "health": {"http_code": $health_code, "body": "$health_body", "status": "pass"},
  "models": $(echo "$models_json" | jq '{ object: .object, data: [.data[] | {id, object}] }'),
  "chat_models_available":$(echo "$models_json" | jq -c "[.data[].id] | map(select(. == \"qwen3-8b\" or . == \"qwen2.5-14b\" or . == \"qwen3-32b\"))"),
  "preferred_chat_model": "qwen3-8b",
  "embedding_models_excluded_from_chat": ["bge-m3", "nomic-embed"]
}
EOF
  echo "  preflight evidence written: $OUTPUTS_DIR/preflight.json"
  return 0
}

# ---------- per-case ----------
# Reasoning-only check: content 为空但 reasoning_content 非空,或 content 全空白
is_reasoning_only_or_empty() {
  local raw="$1"
  local content
  content=$(echo "$raw" | jq -r '.choices[0].message.content // ""')
  local reasoning
  reasoning=$(echo "$raw" | jq -r '.choices[0].message.reasoning_content // ""')
  # 全空白当作 empty
  local trimmed
  trimmed=$(echo -n "$content" | tr -d '[:space:]')
  if [ -z "$trimmed" ]; then
    if [ -n "$reasoning" ]; then
      echo "reasoning_only"
    else
      echo "empty"
    fi
    return 0
  fi
  echo "ok"
}

# Length in characters (counts UTF-8 chars, not bytes)
count_chars() {
  printf '%s' "$1" | wc -m | tr -d '[:space:]'
}

# Run a single case
run_case() {
  local case_id="$1"   # e.g. 01-summary
  local case_num="${case_id%%-*}"

  local input_file="$FIXTURES_DIR/$case_id/input.json"
  local out_dir="$OUTPUTS_DIR/$case_id"
  local raw_out="$out_dir/raw-output.json"
  local result_out="$out_dir/assertion-result.json"

  if [ ! -f "$input_file" ]; then
    echo "${C_FAIL}case $case_id: input.json missing at $input_file${C_RESET}"
    return 1
  fi

  mkdir -p "$out_dir"
  echo ""
  echo "${C_INFO}===== case $case_id =====${C_RESET}"

  # POST /v1/chat/completions
  local http_code
  http_code=$(curl -s -m 60 \
    -o "$raw_out" \
    -w "%{http_code}" \
    -H "Content-Type: application/json" \
    -X POST \
    --data @"$input_file" \
    "$BACKEND_BASE/v1/chat/completions" 2>/dev/null || echo "000")

  if [ "$http_code" != "200" ]; then
    echo "  ${C_FAIL}HTTP $http_code${C_RESET} — request failed"
    write_result "$case_id" "fail" "http_$http_code" "" "$result_out"
    return 1
  fi

  # parse content
  local content_status
  content_status=$(is_reasoning_only_or_empty "$(cat "$raw_out")")
  local content
  content=$(jq -r '.choices[0].message.content // ""' "$raw_out")

  case "$content_status" in
    empty)
      echo "  ${C_FAIL}content empty (hard fail)${C_RESET}"
      write_result "$case_id" "fail" "empty_content" "$content" "$result_out"
      return 1
      ;;
    reasoning_only)
      echo "  ${C_FAIL}reasoning-only (content empty, reasoning_content non-empty) — hard fail${C_RESET}"
      write_result "$case_id" "fail" "reasoning_only" "$content" "$result_out"
      return 1
      ;;
  esac

  # 语义 assertion per case
  case "$case_num" in
    01) assert_01_summary "$content" "$case_id" "$result_out" ;;
    02) assert_02_classification "$content" "$case_id" "$result_out" ;;
    03) assert_03_json_extract "$content" "$case_id" "$result_out" ;;
    04) assert_04_chunked_summary "$content" "$case_id" "$result_out" ;;
    05) assert_05_system_anchor "$content" "$case_id" "$result_out" ;;
    *) echo "  unknown case $case_num"; return 1 ;;
  esac
}

# JSON-safe escape (用于把 content 嵌入 result JSON)
json_escape() {
  printf '%s' "$1" | jq -Rs '.'
}

write_result() {
  local case_id="$1" status="$2" reason="$3" content="$4" out="$5"
  local content_json
  content_json=$(json_escape "$content")
  cat > "$out" <<EOF
{
  "case_id": "$case_id",
  "status": "$status",
  "reason": "$reason",
  "content_chars": $(count_chars "$content"),
  "content": $content_json,
  "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF
}

# ---------- assertion implementations ----------

assert_01_summary() {
  local content="$1" case_id="$2" out="$3"
  local len; len=$(count_chars "$content")
  local has_xinglan="no" has_llamaswap="no" has_legacy="no"
  echo "$content" | grep -q "星澜研究院" && has_xinglan="yes"
  echo "$content" | grep -q "llama-swap" && has_llamaswap="yes"
  echo "$content" | grep -qE "Ollama|11434" && has_legacy="yes"

  local pass=true reasons=""
  [ "$has_xinglan" = "yes" ] || { pass=false; reasons="$reasons missing_xinglan"; }
  [ "$has_llamaswap" = "yes" ] || { pass=false; reasons="$reasons missing_llamaswap"; }
  [ "$has_legacy" = "no" ] || { pass=false; reasons="$reasons leaked_legacy_token"; }
  if [ "$len" -lt 12 ] || [ "$len" -gt 60 ]; then pass=false; reasons="$reasons length_out_of_range_${len}"; fi

  if $pass; then
    echo "  ${C_PASS}PASS${C_RESET} (chars=$len, 含 星澜研究院, 含 llama-swap, 不含 legacy)"
    write_result "$case_id" "pass" "all_assertions_pass" "$content" "$out"
    return 0
  else
    echo "  ${C_FAIL}FAIL${C_RESET} ($reasons; chars=$len; content=\"$content\")"
    write_result "$case_id" "fail" "$reasons" "$content" "$out"
    return 1
  fi
}

assert_02_classification() {
  local content="$1" case_id="$2" out="$3"
  # trim whitespace
  local trimmed; trimmed=$(echo "$content" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
  local pass=true reasons=""
  if [ "$trimmed" = "后端契约" ]; then
    : # ok
  else
    pass=false; reasons="not_exact_match_got_'$trimmed'"
  fi
  # 不含其它 2 个候选
  echo "$trimmed" | grep -qE "性能优化|安全审计" && { pass=false; reasons="$reasons contains_other_label"; }

  if $pass; then
    echo "  ${C_PASS}PASS${C_RESET} (精确匹配 后端契约, 不含其它候选)"
    write_result "$case_id" "pass" "all_assertions_pass" "$content" "$out"
    return 0
  else
    echo "  ${C_FAIL}FAIL${C_RESET} ($reasons)"
    write_result "$case_id" "fail" "$reasons" "$content" "$out"
    return 1
  fi
}

assert_03_json_extract() {
  local content="$1" case_id="$2" out="$3"
  # 试图 strip markdown code fence
  local stripped
  stripped=$(echo "$content" | sed -e 's/^```json//' -e 's/^```//' -e 's/```$//' | sed '/^$/d')
  local pass=true reasons=""
  # JSON.parse OK?
  if ! echo "$stripped" | jq -e '.' > /dev/null 2>&1; then
    pass=false; reasons="json_parse_fail"
  else
    # 4 keys 全在
    for k in meeting date owner result; do
      if ! echo "$stripped" | jq -e "has(\"$k\")" > /dev/null 2>&1; then
        pass=false; reasons="$reasons missing_key_$k"
      fi
    done
    # 值精确匹配
    if [ "$pass" = true ]; then
      local v_meeting v_date v_owner v_result
      v_meeting=$(echo "$stripped" | jq -r '.meeting')
      v_date=$(echo "$stripped" | jq -r '.date')
      v_owner=$(echo "$stripped" | jq -r '.owner')
      v_result=$(echo "$stripped" | jq -r '.result')
      [ "$v_meeting" = "P1c Smoke" ] || { pass=false; reasons="$reasons meeting_wrong_'$v_meeting'"; }
      [ "$v_date" = "2026-05-06" ] || { pass=false; reasons="$reasons date_wrong_'$v_date'"; }
      [ "$v_owner" = "Praise" ] || { pass=false; reasons="$reasons owner_wrong_'$v_owner'"; }
      [ "$v_result" = "pass" ] || { pass=false; reasons="$reasons result_wrong_'$v_result'"; }
    fi
  fi

  if $pass; then
    echo "  ${C_PASS}PASS${C_RESET} (JSON parse OK, 4 keys 齐, 值全精确匹配)"
    write_result "$case_id" "pass" "all_assertions_pass" "$content" "$out"
    return 0
  else
    echo "  ${C_FAIL}FAIL${C_RESET} ($reasons; content=\"$content\")"
    write_result "$case_id" "fail" "$reasons" "$content" "$out"
    return 1
  fi
}

assert_04_chunked_summary() {
  local content="$1" case_id="$2" out="$3"
  local len; len=$(count_chars "$content")
  local pass=true reasons=""

  # 长度 60-220
  if [ "$len" -lt 60 ] || [ "$len" -gt 220 ]; then
    pass=false; reasons="length_out_of_range_${len}"
  fi

  # 3 anchor 全命中
  for anchor in P1C_HEALTH P1C_MODELS P1C_ASSERTIONS; do
    echo "$content" | grep -q "$anchor" || { pass=false; reasons="$reasons missing_$anchor"; }
  done

  # 顺序: P1C_HEALTH 在 P1C_MODELS 前, P1C_MODELS 在 P1C_ASSERTIONS 前
  local pos_h pos_m pos_a
  pos_h=$(echo "$content" | grep -bo "P1C_HEALTH" | head -1 | cut -d: -f1)
  pos_m=$(echo "$content" | grep -bo "P1C_MODELS" | head -1 | cut -d: -f1)
  pos_a=$(echo "$content" | grep -bo "P1C_ASSERTIONS" | head -1 | cut -d: -f1)
  if [ -n "$pos_h" ] && [ -n "$pos_m" ] && [ -n "$pos_a" ]; then
    if [ "$pos_h" -ge "$pos_m" ] || [ "$pos_m" -ge "$pos_a" ]; then
      pass=false; reasons="$reasons anchor_order_wrong_h${pos_h}_m${pos_m}_a${pos_a}"
    fi
  fi

  if $pass; then
    echo "  ${C_PASS}PASS${C_RESET} (chars=$len, 3 anchor 顺序正确)"
    write_result "$case_id" "pass" "all_assertions_pass" "$content" "$out"
    return 0
  else
    echo "  ${C_FAIL}FAIL${C_RESET} ($reasons; chars=$len; content=\"$content\")"
    write_result "$case_id" "fail" "$reasons" "$content" "$out"
    return 1
  fi
}

assert_05_system_anchor() {
  local content="$1" case_id="$2" out="$3"
  local pass=true reasons=""

  # 末尾以 LOCAL_LLM_ANCHOR_OK 结尾(去 trailing whitespace)
  local trimmed; trimmed=$(echo "$content" | sed -E 's/[[:space:]]+$//')
  case "$trimmed" in
    *LOCAL_LLM_ANCHOR_OK) : ;;
    *) pass=false; reasons="anchor_not_at_end" ;;
  esac

  # 含 readonly 或 只读
  if ! echo "$content" | grep -qE "readonly|只读"; then
    pass=false; reasons="$reasons missing_readonly_keyword"
  fi

  # 5 forbidden token 0 命中
  for forbidden in 自动化 automation mutation restart "pull model"; do
    if echo "$content" | grep -qF "$forbidden"; then
      pass=false; reasons="$reasons forbidden_$forbidden"
    fi
  done

  if $pass; then
    echo "  ${C_PASS}PASS${C_RESET} (末尾 anchor + readonly + 0 forbidden)"
    write_result "$case_id" "pass" "all_assertions_pass" "$content" "$out"
    return 0
  else
    echo "  ${C_FAIL}FAIL${C_RESET} ($reasons; content=\"$content\")"
    write_result "$case_id" "fail" "$reasons" "$content" "$out"
    return 1
  fi
}

# ---------- main ----------
main() {
  if [ $# -eq 0 ]; then
    cat <<USAGE
Usage:
  bash run-smoke.sh --all
  bash run-smoke.sh --case <01|02|03|04|05>
  bash run-smoke.sh --preflight

Backend: $BACKEND_BASE
Fixtures: $FIXTURES_DIR
Outputs:  $OUTPUTS_DIR
USAGE
    exit 2
  fi

  # 依赖检查
  for cmd in curl jq; do
    if ! command -v $cmd > /dev/null 2>&1; then
      echo "${C_FAIL}missing dependency: $cmd${C_RESET}"
      exit 3
    fi
  done

  case "$1" in
    --preflight)
      run_preflight || exit 1
      exit 0
      ;;
    --case)
      [ -n "${2:-}" ] || { echo "missing case id"; exit 2; }
      run_preflight || exit 1
      local case_id
      case "$2" in
        01) case_id="01-summary" ;;
        02) case_id="02-classification" ;;
        03) case_id="03-json-extract" ;;
        04) case_id="04-chunked-summary" ;;
        05) case_id="05-system-anchor" ;;
        *) echo "unknown case $2"; exit 2 ;;
      esac
      run_case "$case_id"
      exit $?
      ;;
    --all)
      run_preflight || { echo "${C_FAIL}preflight 失败,abort${C_RESET}"; exit 1; }
      local pass_count=0 fail_count=0
      for case_id in 01-summary 02-classification 03-json-extract 04-chunked-summary 05-system-anchor; do
        if run_case "$case_id"; then
          pass_count=$((pass_count + 1))
        else
          fail_count=$((fail_count + 1))
        fi
      done
      echo ""
      echo "${C_INFO}===== summary =====${C_RESET}"
      echo "  pass: $pass_count / 5"
      echo "  fail: $fail_count / 5"
      if [ $fail_count -eq 0 ]; then
        echo "  ${C_PASS}5/5 PASS — proceed to Review 2${C_RESET}"
        exit 0
      else
        echo "  ${C_FAIL}$fail_count case(s) FAILED — verdict per plan v6 §verdict anchor${C_RESET}"
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
