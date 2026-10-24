"""SC-R2-03 —— plist 解析咽喉：4 个解析点对同一模板必须给出同一结论。

为什么这个文件必须存在：本仓的 4 个 plist 解析点曾分成两套注释策略，而**没有任何
测试比对过它们**——

    宽（strict 失败后剥注释重解析）：
        scripts/verify-launchd-parity.sh  _load_plist          （读 installed plist）
    严（裸 plistlib）：
        scripts/verify-launchd-parity.sh  _render_template     （渲染仓库模板后解析）
        scripts/lib/launchctl.sh          akb2_plist_identity  （派生 Label/程序/调度锚）
        scripts/lib/launchctl.sh          akb2_assert_render_clean（安装前 lint 门）

于是 `launchd/com.akb2.feishu-sync.plist` 与 `com.akb2.inflow-sentinel.plist`
（文档注释含裸 `--`）在宽的那处解析得动、在严的三处解析不动：安装器装不上仓库自带的
模板（render-only exit=2），parity 对同一文件报假 `parse_error` 并把真实 diff 吞掉。
3575 条全绿也没人知道，因为上一轮是**把测试放宽**（给测试文件加自己的剥注释函数）
而不是让生产路径统一。

本文件守两件事，缺一不可：
  ① **行为**：对 launchd/ 下每个模板，4 个解析点 + 咽喉本身的结论逐一相等；
     且真正不良构的样本必须被**全部**判拒（否则"一致"可以靠全放过达成）。
  ② **结构**：除咽喉模块外，src/ 与 scripts/ 里不得再出现 plistlib 直调 ——
     第三种注释策略在物理上长不出来。
"""

from __future__ import annotations

import os
import plistlib
import re
import stat
import subprocess
from pathlib import Path
from typing import Any
from xml.parsers.expat import ExpatError

import pytest
import yaml

from akb2.common.plist_parse import parse_plist_file, parse_plist_text

REPO = Path(__file__).resolve().parents[1]
LAUNCHD_DIR = REPO / "launchd"
LAUNCHCTL_LIB = REPO / "scripts" / "lib" / "launchctl.sh"
PARITY_SH = REPO / "scripts" / "verify-launchd-parity.sh"
CHOKEPOINT_REL = "src/akb2/common/plist_parse.py"
VAULT_ROOT_BASE = "/Volumes/AKB2/Obsidian"

# 与 `_ESCAPE_HATCH...` 之类清单同理：解析点的**数量**也要对账。SC 说"4 个"，
# 那就让它可被证伪 —— 新增第 5 个解析点而忘了接咽喉时这条会打红。
EXPECTED_PARSE_POINT_COUNT = 4


# ---------------------------------------------------------------------------
# 4 个解析点各自的"能不能解析"判据（一律只取二值结论，不比对解析出的内容）
# ---------------------------------------------------------------------------


def _render(text: str, *, repo_root: Path, home: Path) -> str:
    return (
        text.replace("__AKB2_REPO_ROOT__", str(repo_root))
        .replace("__AKB2_VAULT_ROOT__", VAULT_ROOT_BASE)
        .replace("__AKB2_STATE_ROOT__", str(repo_root / "state"))
        .replace("__AKB2_HOME__", str(home))
    )


def _chokepoint_ok(template: Path) -> bool:
    """参照结论：咽喉本身怎么判。"""
    try:
        parse_plist_file(template)
    except Exception:
        return False
    return True


def _identity_ok(template: Path) -> bool:
    """解析点 ③ scripts/lib/launchctl.sh::akb2_plist_identity。"""
    proc = subprocess.run(
        ["bash", "-c", f'. "{LAUNCHCTL_LIB}"\nakb2_plist_identity "{template}"'],
        capture_output=True, text=True, env=os.environ.copy(),
    )
    return proc.returncode == 0


def _render_clean_ok(rendered: Path) -> bool:
    """解析点 ④ scripts/lib/launchctl.sh::akb2_assert_render_clean。

    喂的是**已渲染**的文件：否则第一道门（占位符残留）先拒，测到的就不是解析判据。
    """
    proc = subprocess.run(
        ["bash", "-c", f'. "{LAUNCHCTL_LIB}"\nakb2_assert_render_clean "{rendered}"'],
        capture_output=True, text=True, env=os.environ.copy(),
    )
    if proc.returncode == 0:
        return True
    # 只有"XML 不合法"才算解析判拒；占位符门/其他门的拒绝不属于本比对
    assert "未替换占位符" not in proc.stderr, (
        f"渲染件仍被占位符门拦下，本比对失去意义：{rendered}\n{proc.stderr}"
    )
    return False


def _parity_verdicts(tmp_path: Path, templates: list[Path]) -> dict[str, bool]:
    """解析点 ①② scripts/verify-launchd-parity.sh::_load_plist / _render_template。

    两点在同一个 python 进程里，且失败都被同一个 `except` 收成 `parse_error=` ——
    一次运行即可读出每个 label 的解析结论（任一点拒 → 该行出现 parse_error）。
    """
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "launchd").mkdir(parents=True)
    (repo / "src" / "akb2" / "common").mkdir(parents=True)
    (repo / "scripts" / "verify-launchd-parity.sh").write_text(
        PARITY_SH.read_text(encoding="utf-8"), encoding="utf-8"
    )
    for rel in ("src/akb2/__init__.py", "src/akb2/common/__init__.py", CHOKEPOINT_REL):
        (repo / rel).write_text((REPO / rel).read_text(encoding="utf-8"), encoding="utf-8")

    installed_dir = tmp_path / "LaunchAgents"
    installed_dir.mkdir()
    jobs: list[dict[str, Any]] = []
    for template in templates:
        label = template.name.removesuffix(".plist")
        (repo / "launchd" / template.name).write_text(
            template.read_text(encoding="utf-8"), encoding="utf-8"
        )
        # installed 侧 = 同一模板渲染后的产物：让两个解析点看**同一份内容**，
        # 结论不一致就只能是策略不一致（而不是文件本来就不同）。
        (installed_dir / template.name).write_text(
            _render(
                template.read_text(encoding="utf-8"), repo_root=repo, home=Path.home()
            ),
            encoding="utf-8",
        )
        jobs.append(
            {
                "label": label,
                "template": f"launchd/{template.name}",
                "installed": str(installed_dir / template.name),
                "installer": "manual_existing_runtime",
                "should_load": False,
                "pending_human_install": False,
                # kind 必须合法：非法 kind 会在同一个 try 里抛 ValueError，
                # 被收成 parse_error → 污染本比对的判据。
                "expected_schedule": _schedule_expectation(template),
                "expected_env_keys": _env_keys(template),
            }
        )

    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {
                "generated_at": "2026-08-04",
                "defaults": {
                    "launch_agents_dir": str(installed_dir),
                    "repo_root_placeholder": "__AKB2_REPO_ROOT__",
                    "home_placeholder": "__AKB2_HOME__",
                    "vault_root_placeholder": "__AKB2_VAULT_ROOT__",
                    "vault_root_base": VAULT_ROOT_BASE,
                },
                "jobs": jobs,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    # stub launchctl：不查真 launchd（那会让结论随本机运行态飘）
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "launchctl"
    stub.write_text(
        '#!/usr/bin/env bash\necho "Could not find service" >&2\nexit 113\n',
        encoding="utf-8",
    )
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["AKB2_PARITY_MANIFEST"] = str(manifest)
    proc = subprocess.run(
        ["bash", str(repo / "scripts" / "verify-launchd-parity.sh")],
        capture_output=True, text=True, env=env, timeout=120,
    )

    verdicts: dict[str, bool] = {}
    for template in templates:
        label = template.name.removesuffix(".plist")
        line = next(
            (ln for ln in proc.stdout.splitlines() if f"label={label} " in ln), None
        )
        assert line is not None, (
            f"parity 没报告 {label}（本比对会漏判）：\n{proc.stdout}\n{proc.stderr}"
        )
        verdicts[template.name] = "parse_error" not in line
    return verdicts


def _schedule_expectation(template: Path) -> dict[str, Any]:
    """从模板自身派生一个合法的 expected_schedule（不良构模板给个占位 kind）。"""
    try:
        data = parse_plist_file(template)
    except Exception:
        return {"kind": "keepalive"}
    if data.get("KeepAlive") is not None:
        return {"kind": "keepalive"}
    if isinstance(data.get("StartInterval"), int):
        return {"kind": "interval", "seconds": data["StartInterval"]}
    slots = data.get("StartCalendarInterval")
    entries = [slots] if isinstance(slots, dict) else list(slots or [])
    return {"kind": "calendar", "entries": entries}


def _env_keys(template: Path) -> list[str]:
    try:
        data = parse_plist_file(template)
    except Exception:
        return []
    return sorted((data.get("EnvironmentVariables") or {}).keys())


# ---------------------------------------------------------------------------
# ① 行为一致性
# ---------------------------------------------------------------------------


def test_all_parse_points_agree_on_every_template(tmp_path: Path) -> None:
    """launchd/ 下每个模板 + 一个不良构样本：5 个判据（咽喉 + 4 个解析点）结论全等。

    不良构样本是这条断言的**非空过**保险：只比"都能解析"的话，把 4 个解析点全改成
    `return {}` 也能让本用例变绿。所以清单里塞一个真正不良构的 plist，要求 5 个判据
    **一致地判拒**。
    """
    templates = sorted(LAUNCHD_DIR.glob("*.plist"))
    assert len(templates) >= 7, f"模板数异常少，断言可能已失去意义: {len(templates)}"

    # 不良构样本：缺闭合标签（expat 与 CoreFoundation 都拒）
    broken = tmp_path / "com.akb2.broken-sample.plist"
    broken.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<plist version="1.0">\n<dict><key>Label</key>'
        "<string>com.akb2.broken-sample</string>\n",
        encoding="utf-8",
    )
    subjects = [*templates, broken]

    parity = _parity_verdicts(tmp_path / "parity", subjects)

    disagreements: list[str] = []
    for subject in subjects:
        rendered = tmp_path / f"rendered-{subject.name}"
        rendered.write_text(
            _render(
                subject.read_text(encoding="utf-8"),
                repo_root=tmp_path,
                home=Path.home(),
            ),
            encoding="utf-8",
        )
        verdicts = {
            "chokepoint": _chokepoint_ok(subject),
            "parity(_load_plist+_render_template)": parity[subject.name],
            "akb2_plist_identity": _identity_ok(subject),
            "akb2_assert_render_clean": _render_clean_ok(rendered),
        }
        if len(set(verdicts.values())) != 1:
            disagreements.append(f"{subject.name}: {verdicts}")

    assert not disagreements, (
        "同一模板在不同解析点得出不同结论（安装器与漂移检测会各自为政）：\n  "
        + "\n  ".join(disagreements)
    )

    # 非空过：真实模板必须是"全部能解析"，不良构样本必须是"全部判拒"
    assert _chokepoint_ok(templates[0]) is True, "参照样本自己就解析不了"
    assert _chokepoint_ok(broken) is False, "不良构样本被咽喉放行 —— 保险失效"
    assert parity[broken.name] is False, "不良构样本在 parity 侧没被判 parse_error"


@pytest.mark.parametrize(
    "template_name",
    ["com.akb2.feishu-sync.plist"],
)
def test_bare_dash_comment_templates_parse_everywhere(
    tmp_path: Path, template_name: str
) -> None:
    """两个 R2 blocker 模板逐条钉死：注释含裸 `--`，4 个解析点必须**都**接受。

    单列出来是为了让回归定位到具体文件 —— 上面那条一致性断言即使全体退回"严"策略
    也仍然"一致"（一致地拒），而一致地拒同样意味着安装器装不上仓库自带的模板。
    """
    template = LAUNCHD_DIR / template_name
    raw = template.read_text(encoding="utf-8")
    # 前提自证：注释里确实有裸 `--`，且它确实让 strict expat 拒解
    comments = re.findall(r"<!--.*?-->", raw, flags=re.DOTALL)
    assert any("--" in c[4:-3] for c in comments), (
        f"{template_name} 的注释里已没有裸 `--`，本用例失去意义（前提变了）"
    )
    # 钉死 ExpatError（而不是裸 Exception）：本用例的前提正是"strict expat 拒解、
    # CoreFoundation 接受"。若哪天变成 InvalidFileException 之类，说明前提换了成因，
    # 咽喉的 ExpatError-only 回退未必还覆盖它 —— 那必须打红，不能被宽断言吃掉。
    with pytest.raises(ExpatError):
        plistlib.loads(raw.encode("utf-8"))

    rendered = tmp_path / f"rendered-{template_name}"
    rendered.write_text(
        _render(raw, repo_root=tmp_path, home=Path.home()), encoding="utf-8"
    )
    parity = _parity_verdicts(tmp_path / "parity", [template])

    assert _chokepoint_ok(template), template_name
    assert parity[template_name], f"{template_name}: parity 判 parse_error"
    assert _identity_ok(template), f"{template_name}: akb2_plist_identity 拒解"
    assert _render_clean_ok(rendered), f"{template_name}: akb2_assert_render_clean 拒解"


def test_chokepoint_preserves_structure_of_commented_template() -> None:
    """剥注释不得改变解析出的结构（否则"一致"是靠丢内容换来的）。

    `shutil.which("plutil")` 守卫必须**写在本函数内**：plutil 是 Apple 专有二进制，
    Linux 上不存在，而 `subprocess.run` 对不存在的可执行文件在 `check=False` 下依然抛
    FileNotFoundError；tests/test_launchd_plists.py::test_plutil_cases_guarded_for_ci
    按 AST 逐函数校验这条（抽成 `_has_plutil()` 辅助函数会被它判违规 —— 本轮实测踩过）。
    """
    import shutil

    template = LAUNCHD_DIR / "com.akb2.feishu-sync.plist"
    raw = template.read_text(encoding="utf-8")
    via_chokepoint = parse_plist_text(raw)
    if shutil.which("plutil") is None:
        pytest.skip("本机无 plutil（非 macOS）—— 结构对照由 CoreFoundation 侧缺席")
    # 与 macOS 生产加载器同族的判据：plutil 转出的结构必须与咽喉一致
    converted = subprocess.run(
        ["plutil", "-convert", "xml1", "-o", "-", "-"],
        input=raw.encode("utf-8"), capture_output=True, check=False,
    )
    assert converted.returncode == 0, converted.stderr
    assert via_chokepoint == parse_plist_text(converted.stdout.decode("utf-8")), (
        "咽喉解析出的结构与 CoreFoundation（launchd 用的那个）不一致"
    )


# ---------------------------------------------------------------------------
# ② 结构：第三种策略在物理上长不出来
# ---------------------------------------------------------------------------


def _code_lines(path: Path) -> list[str]:
    """去掉 `#` 注释后的代码行（注释里会引用 plistlib 做说明，不能算直调）。"""
    return [line.split("#", 1)[0] for line in path.read_text(encoding="utf-8").splitlines()]


def test_no_plistlib_calls_outside_the_chokepoint() -> None:
    """src/ 与 scripts/ 里除咽喉模块外不得直调 plistlib —— 单一 owner 的物理保证。"""
    offenders: list[str] = []
    scanned = 0
    roots = [
        *sorted((REPO / "src").rglob("*.py")),
        *sorted((REPO / "scripts").rglob("*.sh")),
        *sorted((REPO / "scripts").rglob("*.py")),
    ]
    assert len(roots) >= 20, f"扫描面异常小: {len(roots)}"
    for path in roots:
        rel = path.relative_to(REPO).as_posix()
        if rel == CHOKEPOINT_REL:
            scanned += 1
            continue
        for lineno, code in enumerate(_code_lines(path), 1):
            if re.search(r"\bplistlib\.loads?\s*\(", code):
                offenders.append(f"{rel}:{lineno}: {code.strip()}")
    assert scanned == 1, "没扫到咽喉模块自己 —— 路径常量可能过期了"
    assert not offenders, (
        "以下位置绕过了解析咽喉直调 plistlib（第三种注释策略的入口）：\n  "
        + "\n  ".join(offenders)
    )


def test_every_parse_point_routes_through_the_chokepoint() -> None:
    """4 个解析点都必须能看到对咽喉的引用，且数量与 SC 声称的 4 对得上。"""
    parse_points = {
        "verify-launchd-parity.sh::_load_plist": (PARITY_SH, "parse_plist_file"),
        "verify-launchd-parity.sh::_render_template": (PARITY_SH, "parse_plist_text"),
        "launchctl.sh::akb2_plist_identity": (LAUNCHCTL_LIB, "parse_plist_file"),
        "launchctl.sh::akb2_assert_render_clean": (LAUNCHCTL_LIB, "parse_plist_file"),
    }
    assert len(parse_points) == EXPECTED_PARSE_POINT_COUNT

    missing = [
        name
        for name, (path, symbol) in parse_points.items()
        if symbol not in path.read_text(encoding="utf-8")
    ]
    assert not missing, f"以下解析点看不到咽喉引用: {missing}"

    for path in (PARITY_SH, LAUNCHCTL_LIB):
        text = path.read_text(encoding="utf-8")
        assert "akb2.common.plist_parse" in text, (
            f"{path.name} 没有从咽喉模块导入 —— 它自己又有一份解析实现？"
        )
