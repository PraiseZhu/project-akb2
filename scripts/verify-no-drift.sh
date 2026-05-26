#!/usr/bin/env bash
# verify-no-drift.sh — SHA alignment check across all distribution targets
# 用法：verify-no-drift.sh [--json]
#
# 检查项：
#   1. 5 tools: toolbox source vs ~/.codex/skills (Codex install target)
#   2. 5 tools: toolbox source vs AI-Agent Claude capabilities skills mirror
#   3. 5 tools: toolbox source vs AI-Agent Claude capabilities claude-active mirror
#   4. MCP wrapper: toolbox source vs Codex plugin install target
#   5. closure-manifest.json SHA lock
#
# 输出格式（每行）：
#   check=<label> result=MATCH|DRIFT|MISSING [reason=claude_backup_missing] sha=<hash>
#
# 退出码：0=全 MATCH, 1=有 DRIFT 或 MISSING

set -euo pipefail

RED='\033[0;31m'; YEL='\033[0;33m'; GRN='\033[0;32m'; CYN='\033[0;36m'; NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TOOLBOX="$ROOT/toolbox"

CODEX_SKILLS="$HOME/.codex/skills"
WORKSPACE="$HOME/AI-Agent/Claude/capabilities/source"
CLAUDE_BACKUP="$WORKSPACE/skills"
CLAUDE_ACTIVE="$WORKSPACE/skills/claude-active"
MCP_TARGET="$HOME/AI-Agent/Codex/plugins/claude-code-ai-infra/mcp-servers/akb-search-readonly/server.py"

CLOSURE_MANIFEST="$ROOT/src/akb2/closure/2026-05-07/closure-manifest.json"
CLOSURE_EXPECTED_SHA="0fbd53703ff58802299ba8cb04f5290e4b973c124729376dc341f862f06f90cc"

ALL_SLUGS=(executable-plan review local-llm skill-stocktake akb-search)

JSON_MODE=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --json) JSON_MODE=true; shift ;;
    -h|--help)
      echo "Usage: $0 [--json]"
      echo "  --json    output JSON instead of human-readable"
      exit 0 ;;
    *) echo -e "${RED}[ERROR]${NC} Unknown arg: $1"; exit 1 ;;
  esac
done

TOTAL=0
FAILURES=0
RESULTS=()

# Helper: compute SHA for a file, return MISSING if not found
file_sha() {
  local path="$1"
  if [[ -f "$path" ]]; then
    shasum -a 256 "$path" | awk '{print $1}'
  else
    echo "MISSING"
  fi
}

# Helper: emit one check result
emit() {
  local label="$1" result="$2" sha="$3" reason="${4:-}"
  TOTAL=$((TOTAL + 1))

  local line="check=$label result=$result sha=$sha"
  if [[ -n "$reason" ]]; then
    line="$line reason=$reason"
  fi
  RESULTS+=("$line")

  if [[ "$result" != "MATCH" ]]; then
    FAILURES=$((FAILURES + 1))
  fi

  # Human-readable output (unless --json)
  if [[ "$JSON_MODE" == "false" ]]; then
    case "$result" in
      MATCH)   echo -e "${GRN}[MATCH]${NC}   $label  sha=$sha" ;;
      DRIFT)   echo -e "${YEL}[DRIFT]${NC}   $label  sha=$sha${reason:+  reason=$reason}" ;;
      MISSING) echo -e "${RED}[MISSING]${NC} $label  sha=$sha${reason:+  reason=$reason}" ;;
    esac
  fi
}

[[ "$JSON_MODE" == "false" ]] && echo -e "${CYN}=== verify-no-drift ===${NC}"
[[ "$JSON_MODE" == "false" ]] && echo ""

# === 1. Codex install target (5 tools SKILL.md) ===
[[ "$JSON_MODE" == "false" ]] && echo "--- Codex install target ---"
for slug in "${ALL_SLUGS[@]}"; do
  SRC_SHA=$(file_sha "$TOOLBOX/cc-$slug/SKILL.md")
  DST_SHA=$(file_sha "$CODEX_SKILLS/cc-$slug/SKILL.md")

  if [[ "$SRC_SHA" == "MISSING" ]]; then
    emit "codex:cc-$slug" "MISSING" "$SRC_SHA" "source_missing"
  elif [[ "$DST_SHA" == "MISSING" ]]; then
    emit "codex:cc-$slug" "MISSING" "$SRC_SHA" "target_missing"
  elif [[ "$SRC_SHA" == "$DST_SHA" ]]; then
    emit "codex:cc-$slug" "MATCH" "$SRC_SHA"
  else
    emit "codex:cc-$slug" "DRIFT" "$SRC_SHA"
  fi
done

# === 2. Claude backup mirror (5 tools SKILL.md) ===
[[ "$JSON_MODE" == "false" ]] && echo ""
[[ "$JSON_MODE" == "false" ]] && echo "--- Claude backup mirror ---"
for slug in "${ALL_SLUGS[@]}"; do
  SRC_SHA=$(file_sha "$TOOLBOX/cc-$slug/SKILL.md")
  DST_SHA=$(file_sha "$CLAUDE_BACKUP/cc-$slug/SKILL.md")

  if [[ "$DST_SHA" == "MISSING" ]]; then
    emit "claude-backup:cc-$slug" "MISSING" "$SRC_SHA" "claude_backup_missing"
  elif [[ "$SRC_SHA" == "$DST_SHA" ]]; then
    emit "claude-backup:cc-$slug" "MATCH" "$SRC_SHA"
  else
    emit "claude-backup:cc-$slug" "DRIFT" "$SRC_SHA"
  fi
done

# === 3. Claude active mirror (5 tools SKILL.md) ===
[[ "$JSON_MODE" == "false" ]] && echo ""
[[ "$JSON_MODE" == "false" ]] && echo "--- Claude active mirror ---"
for slug in "${ALL_SLUGS[@]}"; do
  SRC_SHA=$(file_sha "$TOOLBOX/cc-$slug/SKILL.md")
  DST_SHA=$(file_sha "$CLAUDE_ACTIVE/cc-$slug/SKILL.md")

  if [[ "$DST_SHA" == "MISSING" ]]; then
    emit "claude-active:cc-$slug" "MISSING" "$SRC_SHA" "claude_backup_missing"
  elif [[ "$SRC_SHA" == "$DST_SHA" ]]; then
    emit "claude-active:cc-$slug" "MATCH" "$SRC_SHA"
  else
    emit "claude-active:cc-$slug" "DRIFT" "$SRC_SHA"
  fi
done

# === 4. MCP wrapper ===
[[ "$JSON_MODE" == "false" ]] && echo ""
[[ "$JSON_MODE" == "false" ]] && echo "--- MCP wrapper ---"
MCP_SRC="$TOOLBOX/cc-akb-search/mcp_server/server.py"
MCP_SRC_SHA=$(file_sha "$MCP_SRC")
MCP_DST_SHA=$(file_sha "$MCP_TARGET")

if [[ "$MCP_SRC_SHA" == "MISSING" ]]; then
  emit "mcp:akb-search-readonly" "MISSING" "$MCP_SRC_SHA" "source_missing"
elif [[ "$MCP_DST_SHA" == "MISSING" ]]; then
  emit "mcp:akb-search-readonly" "MISSING" "$MCP_SRC_SHA" "target_missing"
elif [[ "$MCP_SRC_SHA" == "$MCP_DST_SHA" ]]; then
  emit "mcp:akb-search-readonly" "MATCH" "$MCP_SRC_SHA"
else
  emit "mcp:akb-search-readonly" "DRIFT" "$MCP_SRC_SHA"
fi

# === 5. closure-manifest SHA lock ===
[[ "$JSON_MODE" == "false" ]] && echo ""
[[ "$JSON_MODE" == "false" ]] && echo "--- closure-manifest SHA lock ---"
CLOSURE_SHA=$(file_sha "$CLOSURE_MANIFEST")

if [[ "$CLOSURE_SHA" == "MISSING" ]]; then
  emit "closure-manifest" "MISSING" "$CLOSURE_SHA" "file_missing"
elif [[ "$CLOSURE_SHA" == "$CLOSURE_EXPECTED_SHA" ]]; then
  emit "closure-manifest" "MATCH" "$CLOSURE_SHA"
else
  emit "closure-manifest" "DRIFT" "$CLOSURE_SHA"
fi

# === Summary ===
[[ "$JSON_MODE" == "false" ]] && echo ""

if [[ "$JSON_MODE" == "true" ]]; then
  # JSON output
  echo "{"
  echo "  \"total\": $TOTAL,"
  echo "  \"failures\": $FAILURES,"
  echo "  \"checks\": ["
  for i in "${!RESULTS[@]}"; do
    SEP=","
    [[ $i -eq $((${#RESULTS[@]} - 1)) ]] && SEP=""
    echo "    \"${RESULTS[$i]}\"$SEP"
  done
  echo "  ]"
  echo "}"
else
  if [[ $FAILURES -eq 0 ]]; then
    echo -e "${GRN}[OK]${NC} all $TOTAL checks MATCH — no drift detected"
  else
    echo -e "${YEL}[DRIFT]${NC} $FAILURES/$TOTAL checks failed"
  fi
fi

exit $([[ $FAILURES -eq 0 ]] && echo 0 || echo 1)
