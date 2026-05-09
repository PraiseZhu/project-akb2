#!/usr/bin/env bash
# sync-mcp-server.sh — MCP wrapper source → Codex install target
# 默认 dry-run；--write 才真正复制
# 用法：sync-mcp-server.sh [--write]
#
# 源：toolbox/cc-akb-search/mcp_server/server.py
# 目标：Codex/plugins/claude-code-ai-infra/mcp-servers/akb-search-readonly/server.py
#
# 禁止行为：
#   - 不启动 MCP server
#   - 不修改 mcp-servers.json
#   - 不修改任何 MCP 配置

set -euo pipefail

RED='\033[0;31m'; YEL='\033[0;33m'; GRN='\033[0;32m'; CYN='\033[0;36m'; NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

SRC="$ROOT/toolbox/cc-akb-search/mcp_server/server.py"
TARGET="$HOME/Library/Mobile Documents/com~apple~CloudDocs/Codex/plugins/claude-code-ai-infra/mcp-servers/akb-search-readonly/server.py"

MODE=dry-run

while [[ $# -gt 0 ]]; do
  case "$1" in
    --write) MODE=write; shift ;;
    -h|--help)
      echo "Usage: $0 [--write]"
      echo "  --write   actually copy file (default: dry-run)"
      echo ""
      echo "Source: toolbox/cc-akb-search/mcp_server/server.py"
      echo "Target: Codex/plugins/.../akb-search-readonly/server.py"
      exit 0 ;;
    *) echo -e "${RED}[ERROR]${NC} Unknown arg: $1"; exit 1 ;;
  esac
done

echo -e "${CYN}=== sync-mcp-server ===${NC}"
echo "mode:   $MODE"
echo "source: $SRC"
echo "target: $TARGET"
echo ""

# Source check
if [[ ! -f "$SRC" ]]; then
  echo -e "${RED}[ERROR]${NC} source missing: $SRC"
  exit 1
fi

SRC_SHA=$(shasum -a 256 "$SRC" | awk '{print $1}')
echo "source SHA: $SRC_SHA"

# Target check
if [[ ! -f "$TARGET" ]]; then
  echo -e "${YEL}[WARN]${NC} target missing: $TARGET"
  DST_SHA="MISSING"
else
  DST_SHA=$(shasum -a 256 "$TARGET" | awk '{print $1}')
fi
echo "target SHA: $DST_SHA"

if [[ "$SRC_SHA" == "$DST_SHA" ]]; then
  echo -e "${GRN}[MATCH]${NC} source and target are identical"
else
  echo -e "${YEL}[DRIFT]${NC} source and target differ"
fi

if [[ "$MODE" == "write" ]]; then
  TARGET_DIR="$(dirname "$TARGET")"
  if [[ ! -d "$TARGET_DIR" ]]; then
    echo -e "${RED}[ERROR]${NC} target directory missing: $TARGET_DIR"
    echo "       will not create install target directory (must exist)"
    exit 1
  fi
  cp "$SRC" "$TARGET"
  echo -e "${GRN}[WRITTEN]${NC} $SRC → $TARGET"
else
  echo -e "${CYN}[DRY-RUN]${NC} would cp $SRC → $TARGET"
fi

echo ""
echo -e "${GRN}[OK]${NC} sync-mcp-server complete (mode=$MODE)"
