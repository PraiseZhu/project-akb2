#!/usr/bin/env bash
# akb-pre-commit.sh — AKB 项目 pre-commit 防护
# 拦截：>1MB 新文件、.icloud 占位、secret 模式
# 装入：Project AKB2/.git/hooks/pre-commit (软链或拷贝)

set -uo pipefail

RED='\033[0;31m'; YEL='\033[0;33m'; GRN='\033[0;32m'; NC='\033[0m'

# 取本次 commit 即将加入的文件清单（已 staged 的）
STAGED=$(git diff --cached --name-only --diff-filter=ACM)

[[ -z "$STAGED" ]] && exit 0  # 无 staged，让 git 自己处理

FAIL=0
LOCK=$(git rev-parse --show-toplevel)

# 跨平台文件字节数（BSD `stat -f %z` / GNU `stat -c %s`，按 uname 分支——
# 同 akb2-watchdog.sh 的既有做法）。
#
# 原写法 `stat -f %z … 2>/dev/null || echo 0` 在 Linux 上不是"取不到就当 0"，
# 而是更坏：GNU stat 的 `-f` 是 **filesystem 模式**，`%z` 被当成文件名报错，
# 同时目标文件被当成查询对象输出整段文件系统信息 → SZ 拿到多行文本 →
# `[[ $SZ -gt … ]]` 算术比较失效 → **大文件闸门整体静默放行**（违宪法 P4）。
#
# 取不到大小时返回 1 而不是 0：调用方必须 fail-closed 拦住，
# 不能因为"量不出来"就放行（宪法 P3）。
file_size_bytes() {
  local f="$1" sz
  if [ "$(uname -s)" = "Darwin" ]; then
    sz=$(stat -f %z "$f" 2>/dev/null) || sz=""
  else
    sz=$(stat -c %s "$f" 2>/dev/null) || sz=""
  fi
  [[ "$sz" =~ ^[0-9]+$ ]] || return 1
  printf '%s' "$sz"
}

# === 1. 大文件检查（>1MB） ===
while IFS= read -r f; do
  [[ -f "$LOCK/$f" ]] || continue
  if ! SZ=$(file_size_bytes "$LOCK/$f"); then
    echo -e "${RED}[拦截]${NC} 无法读取文件大小（stat 失败，无法判定是否超限）: $f"
    echo -e "       → 大文件闸门不能在量不出大小时放行；请检查文件可读性"
    FAIL=1
    continue
  fi
  if [[ $SZ -gt 1048576 ]]; then
    HUMAN=$(awk "BEGIN{printf \"%.1fM\", $SZ/1048576}")
    echo -e "${RED}[拦截]${NC} 大文件 ($HUMAN): $f"
    echo -e "       → 应加入 .gitignore，或用 git lfs"
    FAIL=1
  fi
done <<< "$STAGED"

# === 2. iCloud 占位文件检查 ===
ICLOUD=$(echo "$STAGED" | grep -E '\.icloud$' || true)
if [[ -n "$ICLOUD" ]]; then
  echo -e "${RED}[拦截]${NC} .icloud 占位文件（未下载）："
  echo "$ICLOUD" | sed 's/^/       /'
  echo "       → 在 Finder 打开父目录触发下载，或加入 .gitignore"
  FAIL=1
fi

# === 3. 文件名级 secret 排除 ===
SECRET_FILES=$(echo "$STAGED" | grep -E '(^|/)(\.env$|\.env\.[a-z]+$|.*\.key$|.*\.pem$|credentials.*|.*-cookies\.txt$|.*\.token\.json$|\.auth\.json$)' | grep -vE '\.env\.example$' || true)
if [[ -n "$SECRET_FILES" ]]; then
  echo -e "${RED}[拦截]${NC} 敏感文件名："
  echo "$SECRET_FILES" | sed 's/^/       /'
  echo "       → 应加入 .gitignore"
  FAIL=1
fi

# === 4. 内容 secret 模式扫描 ===
# 只扫描文本文件（跳过二进制）
PATTERNS='(sk-[a-zA-Z0-9_-]{20,}|ghp_[a-zA-Z0-9]{30,}|gho_[a-zA-Z0-9]{30,}|AKIA[0-9A-Z]{16}|-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----|eyJ[A-Za-z0-9_-]{20,}\.eyJ[A-Za-z0-9_-]{20,}\.)'
while IFS= read -r f; do
  [[ -f "$LOCK/$f" ]] || continue
  # 跳过二进制
  if file "$LOCK/$f" 2>/dev/null | grep -q "binary"; then continue; fi
  HIT=$(grep -E "$PATTERNS" "$LOCK/$f" 2>/dev/null | head -1 || true)
  if [[ -n "$HIT" ]]; then
    echo -e "${RED}[拦截]${NC} 疑似 secret 在: $f"
    echo "       → 请检查并清理后再 commit"
    FAIL=1
  fi
done <<< "$STAGED"

# === 5. 大数据目录通配检查 ===
LEAK_DIRS=$(echo "$STAGED" | grep -E '^(raw/|raw-pdf|index/|notes/|translated/|_archive/|history/|toolbox/|test-xhs-output/)' || true)
if [[ -n "$LEAK_DIRS" ]]; then
  echo -e "${RED}[拦截]${NC} 大数据目录文件溜进 commit："
  echo "$LEAK_DIRS" | head -5 | sed 's/^/       /'
  echo "       → 检查 .gitignore 是否被覆盖"
  FAIL=1
fi

if [[ $FAIL -eq 1 ]]; then
  echo ""
  echo -e "${RED}=== Pre-commit 拦截 ===${NC}"
  echo "如确认要绕过此检查（不推荐）：git commit --no-verify"
  exit 1
fi

echo -e "${GRN}✓${NC} pre-commit 检查通过"
exit 0
