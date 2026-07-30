"""全仓 shell 可移植性守卫（BSD/macOS ↔ GNU/Linux）。

为什么需要静态守卫：本仓库日常开发在 macOS（BSD userland），CI 跑在
ubuntu-latest（GNU userland）。BSD-only 语法在本机全绿、只在 CI 炸，而 CI 炸
的时候人往往已经在读别的日志了 —— 生产实证 PR #65：
`mktemp -t akb2-backstop-read`（BSD 语法，GNU 缺 XXXXXX 直接 rc=1）让
tests/briefing 整片红，且红的 PR 被合进了 main，靠 #66 才 fix-forward。

原守卫（#66）只 parametrize 两个 backstop 脚本，抓不到别处的同类问题 ——
本文件把范围扩到 `scripts/*.sh` 全量，backstop 自然被覆盖。

设计要点：
- **不是"BSD 语法一律禁"**：`stat -f %z` 在按 `uname -s` 分支后是正确写法
  （见 `akb2-watchdog.sh:file_age_sec`）。所以 stat 一类要求「同函数内有
  uname 分支」而不是禁用。
- **豁免要显式且带理由**：平台专用脚本（如 mac mini 部署）不该为跨平台买单，
  但豁免必须写在 `PLATFORM_SPECIFIC_SCRIPTS` 里让人看见，不能靠"忘了扫"。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"

# 平台专用脚本豁免清单：仅在该平台执行，不进 CI 的跨平台路径。
# 加入这里必须写明理由 —— 豁免是决定，不是遗漏。
PLATFORM_SPECIFIC_SCRIPTS: dict[str, str] = {
    # 名字即契约：只在 mac mini（Darwin）上跑，BSD stat/chmod 语义是前提。
    "deploy-mac-mini.sh": "mac mini 专用部署脚本，仅 Darwin 执行",
}

# (正则, 说明) —— 只收「两端语义不同且会硬失败」的构造。
#
# mktemp 这条按「-t 且模板不含 XXXXXX」判定：GNU 支持 `-t` 加含 XXXXXX 的模板
# （实测 GNU coreutils 9.11：`mktemp -t com.akb2.x.XXXXXX.plist` 正常返回），
# 所以 bootstrap_*.sh 的既有写法合规。真正致命的只有「-t 且没有 X」。
BSD_ONLY_PATTERNS: list[tuple[str, str]] = [
    (r"\bdate\s+-v", "date -v：BSD 专用，GNU 是 date -d"),
    (r"\bdate\s+-j", "date -j：BSD 专用"),
    (r"sed\s+-i\s+''", "sed -i '' ：BSD 专用，GNU 是 sed -i"),
    (r"\bsed\s+-i\s+-e", "sed -i -e：BSD 会把下一个参数当备份后缀"),
    (r"\breadlink\s+-f\b", "readlink -f：旧版 macOS 无此选项"),
    (r"\bxargs\s+-r\b", "xargs -r：GNU 专用，BSD xargs 无此选项"),
    (r"\bgrep\s+-P\b", "grep -P：GNU 专用，BSD grep 无 PCRE"),
]

# stat 平台分支要求。GNU 的 `-f` 是 filesystem 模式（`%z` 被当文件名），BSD 的
# `-c` 不存在 —— 两边都不是"取不到值"，而是拿到垃圾/报错，静默兜底会让闸门失效。
#
# 判据是**两种写法必须成对出现**，而不是"脚本里有 uname 就放过"：后者是弱守卫，
# 一个自带 uname 的辅助函数会把同文件里别处的单平台写法一起掩护掉（本守卫初版
# 就是这样漏掉回退用例的，非空过实测发现）。成对出现才说明两个平台都写了分支。
STAT_BSD_RE = re.compile(r"\bstat\s+-f\s+['\"]?%")
STAT_GNU_RE = re.compile(r"\bstat\s+-c\s+['\"]?%")
UNAME_BRANCH_RE = re.compile(r"uname\s+-s|OSTYPE|\$\(uname\)")


def _shell_scripts() -> list[Path]:
    return sorted(p for p in SCRIPTS_DIR.glob("*.sh") if p.is_file())


def _code_lines(script: Path) -> list[tuple[int, str]]:
    """去掉注释后的有效代码行（注释里会引用这些写法作说明，不能算违规）。"""
    out: list[tuple[int, str]] = []
    for lineno, line in enumerate(script.read_text(encoding="utf-8").splitlines(), 1):
        code = line.split("#", 1)[0]
        if code.strip():
            out.append((lineno, code))
    return out


def test_scripts_dir_is_scanned() -> None:
    """守卫自身的非空过：范围为空就等于没有守卫。"""
    scripts = _shell_scripts()
    assert len(scripts) >= 5, f"scripts/*.sh 扫描结果异常少: {[p.name for p in scripts]}"
    names = {p.name for p in scripts}
    for expected in ("briefing_backstop.sh", "github_briefing_backstop.sh"):
        assert expected in names, f"{expected} 必须在扫描范围内（#66 守卫的覆盖面不能丢）"


@pytest.mark.parametrize("script", _shell_scripts(), ids=lambda p: p.name)
def test_no_bsd_only_syntax(script: Path) -> None:
    """全仓脚本不得含两端语义不同且会硬失败的构造。"""
    if script.name in PLATFORM_SPECIFIC_SCRIPTS:
        pytest.skip(f"平台专用豁免: {PLATFORM_SPECIFIC_SCRIPTS[script.name]}")
    hits = [
        f"{script.name}:{lineno}: {code.strip()}  ← {why}"
        for lineno, code in _code_lines(script)
        for pattern, why in BSD_ONLY_PATTERNS
        if re.search(pattern, code)
    ]
    assert not hits, "发现跨平台不兼容语法:\n" + "\n".join(hits)


@pytest.mark.parametrize("script", _shell_scripts(), ids=lambda p: p.name)
def test_mktemp_templates_contain_xxxxxx(script: Path) -> None:
    """任何 mktemp 调用的模板必须自带 XXXXXX（GNU 硬要求，缺则 rc=1）。"""
    if script.name in PLATFORM_SPECIFIC_SCRIPTS:
        pytest.skip(f"平台专用豁免: {PLATFORM_SPECIFIC_SCRIPTS[script.name]}")
    for lineno, code in _code_lines(script):
        if "mktemp" not in code:
            continue
        assert "XXXXXX" in code, (
            f"{script.name}:{lineno} 的 mktemp 模板缺 XXXXXX，"
            f"GNU mktemp 会 rc=1「too few X's in template」: {code.strip()}"
        )


@pytest.mark.parametrize("script", _shell_scripts(), ids=lambda p: p.name)
def test_stat_format_usage_is_platform_paired(script: Path) -> None:
    """`stat -f %` 与 `stat -c %` 必须成对出现，且有 uname 分支。

    实测（GNU coreutils 9.11）`stat -f %z FILE`：`%z` 被当文件名报错，FILE 被当
    文件系统查询目标输出整段 fs 信息 —— 变量拿到多行文本，`[[ $x -gt N ]]`
    比较失效。这类"看起来有兜底、实际闸门失效"是宪法 P4 要禁的静默失败。
    """
    if script.name in PLATFORM_SPECIFIC_SCRIPTS:
        pytest.skip(f"平台专用豁免: {PLATFORM_SPECIFIC_SCRIPTS[script.name]}")
    codes = [code for _lineno, code in _code_lines(script)]
    has_bsd = any(STAT_BSD_RE.search(c) for c in codes)
    has_gnu = any(STAT_GNU_RE.search(c) for c in codes)
    if not (has_bsd or has_gnu):
        return
    missing = "stat -c %…（GNU 侧）" if not has_gnu else "stat -f %…（BSD 侧）"
    assert has_bsd and has_gnu, (
        f"{script.name} 只写了单平台 stat 格式化，缺 {missing} —— "
        "另一端不会报错退出，而是拿到垃圾值让判定静默失效（宪法 P4）"
    )
    assert UNAME_BRANCH_RE.search(script.read_text(encoding="utf-8")), (
        f"{script.name} 两种 stat 写法都在但没有 uname 分支 —— 需显式按平台择一"
    )
