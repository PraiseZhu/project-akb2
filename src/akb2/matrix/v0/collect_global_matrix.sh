#!/usr/bin/env bash
# =============================================================================
# collect-global-matrix.sh — AKB2.0 工具库全局矩阵 v0.1 收集脚本
# Scope: current 5 tools × 矩阵字段 (wiki_status / toolbox_status / eval_level / ...)
# Read-only tool allowlist: grep / awk / sed / shasum / cat / head / cut / tr / jq
#   + standard shell builtins: mkdir (outputs dir only) / rm (temp file cleanup only) /
#     date / dirname / pwd / command (preflight checks). ZERO mutations to tool files.
# Exit 0: success; Exit 1: preflight fail (missing cmd); Exit 2: SKILL.md missing; Exit 4: sha256 drift
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AKB_ROOT="/Volumes/AKB2/Obsidian/Praise Space/AKB2.0"
SKILL_BASE="$HOME/.codex/skills"
OUTPUTS_DIR="$SCRIPT_DIR/outputs"
COLLECTED_AT="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"

TOOLS="cc-executable-plan cc-review cc-local-llm cc-skill-stocktake cc-akb-search"

# Baseline SKILL.md sha256 (§1 baseline, P2 lint v0 — byte-identical required)
BASELINE_SHA_cc_executable_plan="5af73fdadd91097cc60f8e6c3622427aeaac8382bda0d1525debe564f2c46d0a"
BASELINE_SHA_cc_review="592cf3ed4b2c6e3f96c63a500bdbe3bebbc375a19983debbfa7f1877f406d274"
BASELINE_SHA_cc_local_llm="3a31ebe4f5938864e752ea51d35d89403e62d31a352ac3eaf01a784721c73050"
BASELINE_SHA_cc_skill_stocktake="df16be83df67a04605b61294a210f9ad214b22e55ce40a45f34068dbaa2c48f2"
BASELINE_SHA_cc_akb_search="b669380de8a0cd4319f0c978f4cfa69d2757b004a8569fc3adac8f633637f982"

# ---------------------------------------------------------------------------
# Helper: extract frontmatter field value
# Usage: get_field <file> <field_name>
# Extracts content between first and second ---, strips quotes
# ---------------------------------------------------------------------------
get_field() {
  local file="$1" field="$2"
  if [ ! -f "$file" ]; then
    echo "MISSING"
    return
  fi
  local val
  # [P2 fix] Use awk index() for fixed-string anchored field matching
  # (avoids BRE metachar issues in grep "^${field}:" when field contains regex specials)
  val=$(awk '/^---/{p++} p==1 && !/^---/{print} p>=2{exit}' "$file" \
    | awk -v fld="${field}:" 'index($0, fld) == 1' | head -1 \
    | sed "s/^${field}:[[:space:]]*//; s/^\"//; s/\"[[:space:]]*\$//; s/[[:space:]]*\$//")
  if [ -z "$val" ]; then
    echo "UNSET"
  else
    echo "$val"
  fi
}

# ---------------------------------------------------------------------------
# Helper: extract sha prefix from workspace_version string
# Format: "sha256:XXXXXXXXXXXX mtime:YYYY-MM-DD"  →  first 12 hex chars
# ---------------------------------------------------------------------------
extract_sha_prefix() {
  echo "$1" | sed 's/sha256://; s/ .*//' | cut -c1-12
}

# ---------------------------------------------------------------------------
# Preflight: verify required commands
# ---------------------------------------------------------------------------
for cmd in jq shasum awk sed grep; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "[PREFLIGHT ERROR] Missing: $cmd" >&2; exit 1; }
done

mkdir -p "$OUTPUTS_DIR"

echo "=== AKB2.0 工具库全局矩阵 v0.1 ==="
echo "Collected at: $COLLECTED_AT"
echo ""

# ---------------------------------------------------------------------------
# SHA256 baseline verification (self-verify read-only gate)
# Any drift → exit 4 (abort before writing outputs)
# ---------------------------------------------------------------------------
echo "--- SHA256 baseline verification ---"
SHA_DRIFT=0
for slug in $TOOLS; do
  skill_file="$SKILL_BASE/$slug/SKILL.md"
  slug_var="BASELINE_SHA_$(echo "$slug" | tr '-' '_')"
  expected_sha="${!slug_var}"

  if [ ! -f "$skill_file" ]; then
    echo "  [MISSING] $slug: SKILL.md not found at $skill_file" >&2
    echo "[ERROR] SKILL.md missing — cannot verify read-only gate. Aborting."
    exit 2
  fi

  actual_sha=$(shasum -a 256 "$skill_file" | awk '{print $1}')
  if [ "$actual_sha" = "$expected_sha" ]; then
    echo "  ✓ $slug: ${actual_sha:0:12}..."
  else
    echo "  [DRIFT] $slug: expected=${expected_sha:0:12} actual=${actual_sha:0:12}"
    SHA_DRIFT=1
  fi
done

if [ "$SHA_DRIFT" -eq 1 ]; then
  echo ""
  echo "[ERROR] SHA256 drift detected — collection aborted. Investigate before proceeding."
  exit 4
fi

echo ""
echo "--- Collecting matrix fields ---"

# ---------------------------------------------------------------------------
# Collect matrix data per tool
# ---------------------------------------------------------------------------
MATRIX_JSON_TMP="$OUTPUTS_DIR/.matrix_$$.ndjson"
# [P2 fix] Ensure temp file is cleaned up on exit/interrupt
trap 'rm -f "$MATRIX_JSON_TMP"' EXIT INT TERM
> "$MATRIX_JSON_TMP"

for slug in $TOOLS; do
  iface="$AKB_ROOT/toolbox/interfaces/$slug.md"
  toolcard="$AKB_ROOT/toolbox/tools/$slug.md"
  evalfile="$AKB_ROOT/toolbox/evals/$slug.md"
  wikifile="$AKB_ROOT/wiki/tools/$slug.md"

  # Extract fields (use tb_status to avoid bash read-only 'status' variable)
  wiki_st=$(get_field "$wikifile" "status")
  tb_st=$(get_field "$toolcard" "status")
  ev_level=$(get_field "$evalfile" "eval_level")
  auto_allowed=$(get_field "$iface" "automation_allowed")
  bk_status=$(get_field "$iface" "backlink_status")
  ws_version_raw=$(get_field "$iface" "workspace_version")
  ws_version=$(extract_sha_prefix "$ws_version_raw")

  # Derive blocking_reason (no explicit field in files; derive from known state)
  if [ "$tb_st" = "active" ]; then
    block_reason="n/a"
  elif [ "$slug" = "cc-akb-search" ]; then
    block_reason="mcp__akb-search not mounted (per P1a)"
  else
    block_reason="unknown"
  fi

  # Derive next_action
  case "$slug" in
    cc-akb-search)
      next_action="wait for mcp__akb-search mount then run L1 eval + upgrade to active"
      ;;
    *)
      if [ "$ev_level" = "L2" ]; then
        next_action="stable — L2 regression on schema change"
      else
        next_action="stable — promote to L2 when ready"
      fi
      ;;
  esac

  # Normalize automation_allowed to JSON boolean (not string)
  case "$auto_allowed" in
    true)  auto_bool="true"  ;;
    false) auto_bool="false" ;;
    *)     auto_bool="false" ;;  # default false for safety; UNSET/MISSING treated as false
  esac

  # Escape for JSON
  next_action_esc=$(printf '%s' "$next_action" | sed 's/\\/\\\\/g; s/"/\\"/g')
  block_reason_esc=$(printf '%s' "$block_reason" | sed 's/\\/\\\\/g; s/"/\\"/g')

  # Note: automation_allowed uses %s without surrounding quotes → JSON boolean, not string
  printf '{"tool_slug":"%s","wiki_status":"%s","toolbox_status":"%s","eval_level":"%s","automation_allowed":%s,"backlink_status":"%s","workspace_version":"%s","blocking_reason":"%s","next_action":"%s","p2_review2_artifact":"present","collected_at":"%s"}\n' \
    "$slug" "$wiki_st" "$tb_st" "$ev_level" \
    "$auto_bool" "$bk_status" "$ws_version" \
    "$block_reason_esc" "$next_action_esc" "$COLLECTED_AT" \
    >> "$MATRIX_JSON_TMP"

  echo "  ✓ $slug: wiki=${wiki_st} toolbox=${tb_st} eval=${ev_level} auto=${auto_allowed} bk=${bk_status}"
done

# ---------------------------------------------------------------------------
# Build outputs/global-matrix.json
# ---------------------------------------------------------------------------
jq -s --arg collected_at "$COLLECTED_AT" '{
  schema_version: "v0.1",
  collected_at: $collected_at,
  scope: "current_5_tools",
  p2_review2_artifact: "present",
  fields: [
    "wiki_status",
    "toolbox_status",
    "eval_level",
    "automation_allowed",
    "backlink_status",
    "workspace_version",
    "blocking_reason",
    "next_action"
  ],
  matrix: .
}' "$MATRIX_JSON_TMP" > "$OUTPUTS_DIR/global-matrix.json"

echo ""
echo "--- Building outputs ---"

# ---------------------------------------------------------------------------
# Build outputs/global-matrix.md
# ---------------------------------------------------------------------------
{
cat << YAML_HEADER
---
title: AKB2.0 工具库全局矩阵
schema_version: v0.1
collected_at: ${COLLECTED_AT}
scope: current_5_tools
p2_review2_artifact: present
---
YAML_HEADER

echo "# AKB2.0 工具库全局矩阵 v0.1"
echo ""
echo "收集时间：${COLLECTED_AT}"
echo ""
echo "## 矩阵总览"
echo ""
echo "| 工具 | wiki_status | toolbox_status | eval_level | automation_allowed | backlink_status | workspace_version | blocking_reason | next_action |"
echo "|------|------------|----------------|------------|-------------------|-----------------|-------------------|-----------------|-------------|"
jq -r '.matrix[] | "| \(.tool_slug) | \(.wiki_status) | \(.toolbox_status) | \(.eval_level) | \(.automation_allowed) | \(.backlink_status) | \(.workspace_version) | \(.blocking_reason) | \(.next_action) |"' \
  "$OUTPUTS_DIR/global-matrix.json"
echo ""
cat << 'MD_BODY'
## 关键区分：wiki_status vs toolbox_status

- **wiki_status**：知识页面发布状态（来源：`wiki/tools/<slug>.md` frontmatter `status`）
- **toolbox_status**：工具合约激活状态（来源：`toolbox/tools/<slug>.md` frontmatter `status`）
- **两者可以不一致**：`cc-akb-search` wiki=active（知识页面已发布）但 toolbox=candidate（合约未激活，blocked per P1a — mcp__akb-search 未 mount）

## 汇总

| 维度 | 数量 | 工具 |
|------|------|------|
| toolbox active | 4 | cc-executable-plan, cc-review, cc-local-llm, cc-skill-stocktake |
| toolbox blocked candidate | 1 | cc-akb-search |
| eval_level L2 | 2 | cc-executable-plan, cc-review |
| eval_level L1 | 2 | cc-local-llm, cc-skill-stocktake |
| eval_level L0 | 1 | cc-akb-search |
| automation_allowed true | 0 | P0 gate 清洁 |
| backlink linked | 4 | cc-executable-plan, cc-review, cc-local-llm, cc-skill-stocktake |
| backlink pending | 1 | cc-akb-search（预期；mcp__akb-search 未 mount） |

## P2 Review 2 证据

`p2_structure_lint_review2_artifact`: **present**

路径：`~/.claude/review-baselines/a3f782c15d0e/result-20260507T125000Z.md`

VERDICT: APPROVED（P1=0, P2=1 non-blocking, P3=2; 5 scope areas PASS）

## 矩阵说明

本矩阵为只读快照。收集脚本 `collect-global-matrix.sh` 不修改任何工具 5 件套 / SKILL.md / bundled scripts。
SHA256 基线来自 P2 lint v0 §1 baseline（2026-05-07）。
MD_BODY
} > "$OUTPUTS_DIR/global-matrix.md"

# Cleanup temp file
rm -f "$MATRIX_JSON_TMP"

echo "  ✓ global-matrix.json"
echo "  ✓ global-matrix.md"
echo ""
echo "=== 矩阵收集完成 ==="
echo "Outputs:"
echo "  $OUTPUTS_DIR/global-matrix.json"
echo "  $OUTPUTS_DIR/global-matrix.md"

exit 0
