#!/usr/bin/env bash
# sync-skills-to-claude-backup.sh — toolbox/ → Claude Code/skills/ + claude-active/ (backup mirror)
# 默认 dry-run；--write 才真正复制
# 用法：sync-skills-to-claude-backup.sh [--all | --slug cc-xxx] [--write]
#
# 双目标：
#   1. Claude Code/skills/cc-{slug}/          ← 备份镜像
#   2. Claude Code/skills/claude-active/cc-{slug}/ ← 活跃镜像
#
# 安全守卫：目标路径解析后若落在 /Users/praise/.claude → 硬停 exit 2

set -euo pipefail

RED='\033[0;31m'; YEL='\033[0;33m'; GRN='\033[0;32m'; CYN='\033[0;36m'; NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TOOLBOX="$ROOT/toolbox"

# Claude Code workspace root
WORKSPACE="$HOME/Library/Mobile Documents/com~apple~CloudDocs/Claude Code"
TARGET_A="$WORKSPACE/skills"
TARGET_B="$WORKSPACE/skills/claude-active"

# 禁止路径（解析后不得落在此目录树内）
FORBIDDEN_PREFIX="$HOME/.claude"

ALL_SLUGS=(executable-plan review local-llm skill-stocktake akb-search)

MODE=dry-run
FILTER=all
SINGLE_SLUG=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --write) MODE=write; shift ;;
    --all) FILTER=all; shift ;;
    --slug)
      [[ $# -lt 2 ]] && { echo -e "${RED}[ERROR]${NC} --slug requires argument"; exit 1; }
      FILTER=single; SINGLE_SLUG="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: $0 [--all | --slug cc-xxx] [--write]"
      echo "  --all     sync all 5 tools (default)"
      echo "  --slug X  sync single tool (e.g. cc-review or review)"
      echo "  --write   actually copy files (default: dry-run)"
      exit 0 ;;
    *) echo -e "${RED}[ERROR]${NC} Unknown arg: $1"; exit 1 ;;
  esac
done

# Resolve single slug
if [[ "$FILTER" == "single" ]]; then
  SINGLE_SLUG="${SINGLE_SLUG#cc-}"
  FOUND=0
  for s in "${ALL_SLUGS[@]}"; do
    [[ "$s" == "$SINGLE_SLUG" ]] && { FOUND=1; break; }
  done
  if [[ $FOUND -eq 0 ]]; then
    echo -e "${RED}[ERROR]${NC} Unknown slug: $SINGLE_SLUG"
    echo "Valid: ${ALL_SLUGS[*]}"
    exit 1
  fi
  SLUGS=("$SINGLE_SLUG")
else
  SLUGS=("${ALL_SLUGS[@]}")
fi

# === 禁止路径守卫 ===
# 解析 TARGET_A 和 TARGET_B 的实际路径，确保不落在 ~/.claude 内
check_forbidden() {
  local label="$1" target="$2"
  # 目标可能不存在，取最近存在的祖先来 resolve
  local resolved="$target"
  if [[ -e "$target" ]]; then
    resolved="$(cd "$target" && pwd -P)"
  else
    # 取父目录 resolve
    local parent
    parent="$(dirname "$target")"
    if [[ -d "$parent" ]]; then
      resolved="$(cd "$parent" && pwd -P)/$(basename "$target")"
    fi
  fi

  local forbidden_resolved
  if [[ -d "$FORBIDDEN_PREFIX" ]]; then
    forbidden_resolved="$(cd "$FORBIDDEN_PREFIX" && pwd -P)"
  else
    forbidden_resolved="$FORBIDDEN_PREFIX"
  fi

  if [[ "$resolved" == "$forbidden_resolved"* ]]; then
    echo -e "${RED}[FATAL]${NC} $label resolves to forbidden path: $resolved"
    echo "       forbidden prefix: $forbidden_resolved"
    echo "       sync-skills-to-claude-backup REFUSES to write to ~/.claude"
    exit 2
  fi
}

check_forbidden "TARGET_A" "$TARGET_A"
check_forbidden "TARGET_B" "$TARGET_B"

echo -e "${CYN}=== sync-skills-to-claude-backup ===${NC}"
echo "mode:     $MODE"
echo "source:   $TOOLBOX/cc-{slug}/"
echo "target A: $TARGET_A/cc-{slug}/"
echo "target B: $TARGET_B/cc-{slug}/"
echo "slugs:    ${SLUGS[*]}"
echo ""

DRIFT=0
ERRORS=0

sync_one_target() {
  local slug="$1" src="$2" dst="$3" label="$4"

  if [[ ! -d "$dst" ]]; then
    DST_SHA="MISSING"
  elif [[ -f "$dst/SKILL.md" ]]; then
    DST_SHA=$(shasum -a 256 "$dst/SKILL.md" | awk '{print $1}')
  else
    DST_SHA="MISSING"
  fi

  SRC_SHA=$(shasum -a 256 "$src/SKILL.md" 2>/dev/null | awk '{print $1}' || echo "MISSING")

  if [[ "$SRC_SHA" == "$DST_SHA" ]]; then
    STATUS="MATCH"
    STATUS_COLOR="$GRN"
  else
    STATUS="DRIFT"
    STATUS_COLOR="$YEL"
    DRIFT=$((DRIFT + 1))
  fi

  echo -e "  $label cc-$slug: ${STATUS_COLOR}${STATUS}${NC}"
  echo "    source SHA: $SRC_SHA"
  echo "    target SHA: $DST_SHA"

  if [[ "$MODE" == "write" ]]; then
    mkdir -p "$dst"
    rsync -av --exclude='.DS_Store' --exclude='__pycache__' --exclude='.ruff_cache' \
      --delete "$src/" "$dst/"
    echo -e "    ${GRN}[WRITTEN]${NC}"
  else
    echo -e "    ${CYN}[DRY-RUN]${NC} would rsync $src/ → $dst/"
  fi
}

for slug in "${SLUGS[@]}"; do
  SRC="$TOOLBOX/cc-$slug"

  if [[ ! -d "$SRC" ]]; then
    echo -e "${RED}[ERROR]${NC} source missing: $SRC"
    ERRORS=$((ERRORS + 1))
    continue
  fi

  echo "cc-$slug:"
  sync_one_target "$slug" "$SRC" "$TARGET_A/cc-$slug" "backup"
  sync_one_target "$slug" "$SRC" "$TARGET_B/cc-$slug" "active"
  echo ""
done

if [[ $ERRORS -gt 0 ]]; then
  echo -e "${RED}[FAIL]${NC} $ERRORS source errors"
  exit 1
fi

echo -e "${GRN}[OK]${NC} sync-skills-to-claude-backup complete (mode=$MODE, drift=$DRIFT)"
