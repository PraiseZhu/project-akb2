#!/usr/bin/env bash
# post-change-pipeline.sh — 工具变更后一键校验 pipeline
# 用法：post-change-pipeline.sh [--write]
#
# Pipeline 顺序（全 dry-run，除非 --write）：
#   1. sync-skills-to-codex        (dry-run)
#   2. sync-skills-to-claude-backup (dry-run)
#   3. sync-mcp-server             (dry-run)
#   4. dry-run no-write invariant  (before/after SHA snapshot 对比)
#   5. verify-no-drift             (SHA alignment check)
#   6. verify-launchd-parity       (write 模式为硬门；exit 3 = 仅已批准 pending，不判失败)
#   7. pytest smoke                (如果全 skip → pytest_skipped_until_step5)
#
# --write 模式：步骤 1-3 实际写入，步骤 4 改为 write-mode invariant 验证
#
# 禁止行为：
#   - 不调用 verify-migration-completeness.sh（不存在）
#   - 不写 TODO 当 pass

set -euo pipefail

RED='\033[0;31m'; YEL='\033[0;33m'; GRN='\033[0;32m'; CYN='\033[0;36m'; NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

MODE=dry-run
WRITE_FLAG=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --write) MODE=write; WRITE_FLAG="--write"; shift ;;
    -h|--help)
      echo "Usage: $0 [--write]"
      echo "  --write   sync scripts actually copy (default: dry-run pipeline)"
      echo ""
      echo "Pipeline: sync-codex → sync-claude-backup → sync-mcp → invariant → verify-no-drift → launchd-parity → pytest"
      exit 0 ;;
    *) echo -e "${RED}[ERROR]${NC} Unknown arg: $1"; exit 1 ;;
  esac
done

PIPELINE_PASS=true
STEP=0

run_step() {
  STEP=$((STEP + 1))
  local label="$1"
  shift
  echo ""
  echo -e "${CYN}[$STEP] $label${NC}"
  echo "    cmd: $*"
  echo ""
  if "$@"; then
    echo -e "${GRN}[$STEP PASS]${NC} $label"
  else
    echo -e "${RED}[$STEP FAIL]${NC} $label"
    PIPELINE_PASS=false
  fi
}

# === Snapshot helpers for dry-run no-write invariant ===

CODEX_SKILLS="$HOME/.codex/skills"
WORKSPACE="$HOME/AI-Agent/Claude/capabilities/source"
CLAUDE_BACKUP="$WORKSPACE/skills"
CLAUDE_ACTIVE="$WORKSPACE/skills/claude-active"
MCP_TARGET="$HOME/AI-Agent/Codex/plugins/claude-code-ai-infra/mcp-servers/akb-search-readonly/server.py"

ALL_SLUGS=(executable-plan review local-llm skill-stocktake akb-search)

SNAPSHOT_FILE_PRE=""
SNAPSHOT_FILE_POST=""

# Compute a snapshot: for each target path, output "path existence sha256"
# For directories: recursive sha256 aggregate (sorted, all files)
# For files: single sha256
# For missing: "MISSING MISSING"
compute_snapshot() {
  local out=""

  # Codex install targets (directories)
  for slug in "${ALL_SLUGS[@]}"; do
    local p="$CODEX_SKILLS/cc-$slug"
    if [[ -d "$p" ]]; then
      local agg
      agg=$(find "$p" -type f -not -name .DS_Store -not -name '*.pyc' -not -path '*/__pycache__/*' \
            -exec shasum -a 256 {} \; 2>/dev/null | sort | shasum -a 256 | awk '{print $1}')
      out+="codex:cc-$slug EXISTS $agg"$'\n'
    else
      out+="codex:cc-$slug MISSING MISSING"$'\n'
    fi
  done

  # Claude backup targets (directories)
  for slug in "${ALL_SLUGS[@]}"; do
    local p="$CLAUDE_BACKUP/cc-$slug"
    if [[ -d "$p" ]]; then
      local agg
      agg=$(find "$p" -type f -not -name .DS_Store -not -name '*.pyc' -not -path '*/__pycache__/*' \
            -exec shasum -a 256 {} \; 2>/dev/null | sort | shasum -a 256 | awk '{print $1}')
      out+="claude-backup:cc-$slug EXISTS $agg"$'\n'
    else
      out+="claude-backup:cc-$slug MISSING MISSING"$'\n'
    fi
  done

  # Claude active targets (directories)
  for slug in "${ALL_SLUGS[@]}"; do
    local p="$CLAUDE_ACTIVE/cc-$slug"
    if [[ -d "$p" ]]; then
      local agg
      agg=$(find "$p" -type f -not -name .DS_Store -not -name '*.pyc' -not -path '*/__pycache__/*' \
            -exec shasum -a 256 {} \; 2>/dev/null | sort | shasum -a 256 | awk '{print $1}')
      out+="claude-active:cc-$slug EXISTS $agg"$'\n'
    else
      out+="claude-active:cc-$slug MISSING MISSING"$'\n'
    fi
  done

  # MCP target (single file)
  if [[ -f "$MCP_TARGET" ]]; then
    local sha
    sha=$(shasum -a 256 "$MCP_TARGET" | awk '{print $1}')
    out+="mcp:akb-search-readonly EXISTS $sha"$'\n'
  else
    out+="mcp:akb-search-readonly MISSING MISSING"$'\n'
  fi

  echo "$out"
}

echo -e "${CYN}=== post-change-pipeline ===${NC}"
echo "mode: $MODE"
echo "root: $ROOT"

# === Pre-snapshot (before any sync) ===
echo ""
echo -e "${CYN}[PRE-SNAPSHOT] Computing target state before sync...${NC}"
SNAPSHOT_PRE=$(compute_snapshot)

# === Step 1: sync-skills-to-codex ===
run_step "sync-skills-to-codex" "$SCRIPT_DIR/sync-skills-to-codex.sh" $WRITE_FLAG

# === Step 2: sync-skills-to-claude-backup ===
run_step "sync-skills-to-claude-backup" "$SCRIPT_DIR/sync-skills-to-claude-backup.sh" $WRITE_FLAG

# === Step 3: sync-mcp-server ===
run_step "sync-mcp-server" "$SCRIPT_DIR/sync-mcp-server.sh" $WRITE_FLAG

# === Step 4: dry-run no-write invariant ===
STEP=$((STEP + 1))
echo ""
echo -e "${CYN}[$STEP] dry-run no-write invariant${NC}"

SNAPSHOT_POST=$(compute_snapshot)

if [[ "$SNAPSHOT_PRE" == "$SNAPSHOT_POST" ]]; then
  if [[ "$MODE" == "dry-run" ]]; then
    echo "dry_run_no_write=true"
    echo -e "${GRN}[$STEP PASS]${NC} dry-run no-write invariant — all targets unchanged"
  else
    echo "write_mode=true (targets may have changed as expected)"
    echo -e "${GRN}[$STEP PASS]${NC} write-mode invariant — snapshot matches (already in sync)"
  fi
else
  if [[ "$MODE" == "dry-run" ]]; then
    echo "dry_run_no_write=false"
    echo -e "${RED}[$STEP FAIL]${NC} dry-run no-write invariant VIOLATED — targets changed during dry-run!"
    echo ""
    echo "=== PRE-SNAPSHOT ==="
    echo "$SNAPSHOT_PRE"
    echo "=== POST-SNAPSHOT ==="
    echo "$SNAPSHOT_POST"
    PIPELINE_PASS=false
  else
    echo "write_mode=true (targets changed as expected)"
    echo -e "${GRN}[$STEP PASS]${NC} write-mode — targets updated"
  fi
fi

# === Step 5: verify-no-drift ===
if [[ "$MODE" == "write" ]]; then
  # After write, drift check should pass
  run_step "verify-no-drift" "$SCRIPT_DIR/verify-no-drift.sh"
else
  # In dry-run, drift check just reports current state (may have pre-existing drift)
  STEP=$((STEP + 1))
  echo ""
  echo -e "${CYN}[$STEP] verify-no-drift (informational in dry-run)${NC}"
  echo "    cmd: $SCRIPT_DIR/verify-no-drift.sh"
  echo ""
  "$SCRIPT_DIR/verify-no-drift.sh" || true
  echo -e "${CYN}[$STEP INFO]${NC} verify-no-drift (drift in dry-run is expected if targets not yet synced)"
fi

# === Step 6: verify-launchd-parity ===
# write 模式是硬门，但 exit 3 = 「只有已批准的 pending_human_install」不算失败：
# manifest 把 inflow-sentinel 登记为 pending 且 docs/allowed-residuals.md 已批准，
# 旧实现让它无条件计 failure → **任何**无关的 write pipeline 都结构性 FAIL，
# 合法状态下 gate 无解（CADV-G4）。真 DRIFT 仍 exit 1 → 仍然 FAIL。
PARITY_PENDING_EXIT_CODE=3
run_step_parity() {
  STEP=$((STEP + 1))
  local rc=0
  echo ""
  echo -e "${CYN}[$STEP] verify-launchd-parity${NC}"
  echo "    cmd: $SCRIPT_DIR/verify-launchd-parity.sh"
  echo ""
  "$SCRIPT_DIR/verify-launchd-parity.sh" || rc=$?
  if [[ "$rc" -eq 0 ]]; then
    echo -e "${GRN}[$STEP PASS]${NC} verify-launchd-parity"
  elif [[ "$rc" -eq "$PARITY_PENDING_EXIT_CODE" ]]; then
    echo -e "${YEL}[$STEP PASS-WITH-PENDING]${NC} verify-launchd-parity（仅已批准的待人工安装）"
  else
    echo -e "${RED}[$STEP FAIL]${NC} verify-launchd-parity (exit=$rc)"
    PIPELINE_PASS=false
  fi
}

if [[ "$MODE" == "write" ]]; then
  run_step_parity
else
  STEP=$((STEP + 1))
  echo ""
  echo -e "${CYN}[$STEP] verify-launchd-parity (informational in dry-run)${NC}"
  echo "    cmd: $SCRIPT_DIR/verify-launchd-parity.sh"
  echo ""
  "$SCRIPT_DIR/verify-launchd-parity.sh" || true
  echo -e "${CYN}[$STEP INFO]${NC} verify-launchd-parity (launchd drift in dry-run is reported, not enforced)"
fi

# === Step 7: pytest smoke ===
STEP=$((STEP + 1))
echo ""
echo -e "${CYN}[$STEP] pytest smoke${NC}"

if command -v pytest &>/dev/null; then
  # Check if all tests are skip placeholders
  # Use subshell + || true to prevent pipefail from aborting when grep finds no matches
  SKIP_COUNT=$({ grep -rl '@pytest.mark.skip' "$ROOT/tests/" 2>/dev/null || true; } | wc -l | tr -d ' ')
  TOTAL_TEST_FILES=$(find "$ROOT/tests/" -name 'test_*.py' -o -name '*_test.py' 2>/dev/null | wc -l | tr -d ' ')

  if [[ "$TOTAL_TEST_FILES" -gt 0 && "$SKIP_COUNT" -eq "$TOTAL_TEST_FILES" ]]; then
    echo "pytest_skipped_until_step5"
    echo -e "${YEL}[$STEP SKIP]${NC} all $TOTAL_TEST_FILES test files are @pytest.mark.skip placeholders"
  elif [[ "$TOTAL_TEST_FILES" -eq 0 ]]; then
    echo "pytest_no_test_files"
    echo -e "${YEL}[$STEP SKIP]${NC} no test files found in tests/"
  else
    if pytest "$ROOT/tests/" -x -q --tb=short 2>&1; then
      echo -e "${GRN}[$STEP PASS]${NC} pytest smoke"
    else
      echo -e "${RED}[$STEP FAIL]${NC} pytest smoke"
      PIPELINE_PASS=false
    fi
  fi
else
  echo "pytest_not_installed"
  echo -e "${YEL}[WARN]${NC} pytest not found in PATH — smoke test skipped (install pytest to enable)"
fi

# === Final Summary ===
echo ""
echo "========================================"
if [[ "$PIPELINE_PASS" == "true" ]]; then
  echo -e "${GRN}[PIPELINE OK]${NC} post-change-pipeline complete (mode=$MODE)"
  exit 0
else
  echo -e "${RED}[PIPELINE FAIL]${NC} post-change-pipeline has failures (mode=$MODE)"
  exit 1
fi
