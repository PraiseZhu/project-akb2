#!/usr/bin/env bash
# sync-skills-to-codex.sh — toolbox/ → ~/.codex/skills/cc-*/ (install target)
# 默认 dry-run；--write 才真正复制
# 用法：sync-skills-to-codex.sh [--all | --slug cc-xxx] [--write]

set -euo pipefail

RED='\033[0;31m'; YEL='\033[0;33m'; GRN='\033[0;32m'; CYN='\033[0;36m'; NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TOOLBOX="$ROOT/toolbox"
TARGET_ROOT="$HOME/.codex/skills"

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

echo -e "${CYN}=== sync-skills-to-codex ===${NC}"
echo "mode:   $MODE"
echo "source: $TOOLBOX/cc-{slug}/"
echo "target: $TARGET_ROOT/cc-{slug}/"
echo "slugs:  ${SLUGS[*]}"
echo ""

DRIFT=0
ERRORS=0

for slug in "${SLUGS[@]}"; do
  SRC="$TOOLBOX/cc-$slug"
  DST="$TARGET_ROOT/cc-$slug"

  if [[ ! -d "$SRC" ]]; then
    echo -e "${RED}[ERROR]${NC} source missing: $SRC"
    ERRORS=$((ERRORS + 1))
    continue
  fi

  # SKILL.md SHA comparison
  SRC_SHA=$(shasum -a 256 "$SRC/SKILL.md" 2>/dev/null | awk '{print $1}' || echo "MISSING")
  if [[ -f "$DST/SKILL.md" ]]; then
    DST_SHA=$(shasum -a 256 "$DST/SKILL.md" | awk '{print $1}')
  else
    DST_SHA="MISSING"
  fi

  if [[ "$SRC_SHA" == "$DST_SHA" ]]; then
    STATUS="MATCH"
    STATUS_COLOR="$GRN"
  else
    STATUS="DRIFT"
    STATUS_COLOR="$YEL"
    DRIFT=$((DRIFT + 1))
  fi

  echo -e "cc-$slug: ${STATUS_COLOR}${STATUS}${NC}"
  echo "  source SKILL.md SHA: $SRC_SHA"
  echo "  target SKILL.md SHA: $DST_SHA"

  # File listing
  SRC_COUNT=$(find "$SRC" -type f -not -name .DS_Store -not -name '*.pyc' -not -path '*/__pycache__/*' | wc -l | tr -d ' ')
  echo "  source file count: $SRC_COUNT"

  if [[ "$MODE" == "write" ]]; then
    mkdir -p "$DST"
    rsync -av --exclude='.DS_Store' --exclude='__pycache__' --exclude='.ruff_cache' \
      --delete "$SRC/" "$DST/"
    echo -e "  ${GRN}[WRITTEN]${NC}"
  else
    echo -e "  ${CYN}[DRY-RUN]${NC} would rsync $SRC/ → $DST/"
  fi
  echo ""
done

if [[ $ERRORS -gt 0 ]]; then
  echo -e "${RED}[FAIL]${NC} $ERRORS source errors"
  exit 1
fi

echo -e "${GRN}[OK]${NC} sync-skills-to-codex complete (mode=$MODE, drift=$DRIFT)"
