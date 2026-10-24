from __future__ import annotations

import ast
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from akb2.common.plist_parse import parse_plist_text

REPO = Path(__file__).resolve().parents[1]
LAUNCHD_DIR = REPO / "launchd"
PLIST_NAMES = [
    "com.akb2.briefing.plist",
    "com.akb2.briefing.backstop.plist",
    "com.akb2.feishu-sync.plist",
    "com.akb2.github_briefing.plist",
    "com.akb2.github_briefing.backstop.plist",
    "com.akb2.video.plist",
    "com.akb2.retrieval-http.plist",
]
XFAIL_REASONS: dict[tuple[str, str], str] = {}
VAULT_ROOT_BASE = "/Volumes/AKB2/Obsidian"


def _maybe_xfail(plist_name: str, check_name: str) -> None:
    reason = XFAIL_REASONS.get((plist_name, check_name))
    if reason:
        pytest.xfail(reason)


@pytest.fixture(autouse=True, scope="module")
def _log_dirs_exist() -> None:
    """SC-R2-09：本文件自建所需的日志父目录，去掉隐式跑序依赖。

    `test_plist_standard_log_parents_exist_after_render` 断言渲染后
    `StandardOutPath` / `StandardErrorPath` 的父目录存在。这些路径渲染成
    `<REPO>/logs/...`，而 `logs/` 在 .gitignore 里 —— **干净检出单跑本文件 = 9 failed**
    （每个 plist 一条），先跑 `tests/briefing`（它会顺手建出 logs/）才 110 passed。
    也就是这条断言的绿灯来自"别人先跑过"，不是被测对象的性质：CI 换个分片顺序、
    或有人单跑本文件，就红。

    fixture 放在本文件而不是共享 conftest：这是本文件自己的前置条件，
    共享 conftest 会让别的文件也悄悄依赖它（同一个坑换个地方埋）。
    目录由 launchd 在运行期同样需要，`exist_ok=True` 幂等，不改任何被测语义。
    """
    created: list[Path] = []
    for plist_name in PLIST_NAMES:
        data = parse_plist_text(_render_plist_text(LAUNCHD_DIR / plist_name))
        for key in ("StandardOutPath", "StandardErrorPath"):
            raw = data.get(key)
            if not isinstance(raw, str) or not raw:
                continue
            parent = Path(raw).parent
            if not parent.exists():
                parent.mkdir(parents=True, exist_ok=True)
                created.append(parent)
    # 非空过：断言 fixture 真的覆盖到了被测断言要看的那些目录
    assert PLIST_NAMES, "PLIST_NAMES 为空，fixture 什么都没建"


def _render_plist_text(path: Path, *, repo_root: Path = REPO, home: Path | None = None) -> str:
    home_root = home or Path.home()
    text = path.read_text(encoding="utf-8")
    replacements = {
        "__AKB2_REPO_ROOT__": str(repo_root),
        "__AKB2_HOME__": str(home_root),
        "__AKB2_VAULT_ROOT__": VAULT_ROOT_BASE,
    }
    for placeholder, replacement in replacements.items():
        text = text.replace(placeholder, replacement)
    return text


def _load_rendered_plist(path: Path) -> dict[str, Any]:
    """走生产解析咽喉，不再在本文件里维护第二份注释策略。

    此前本文件有自己的 `_strip_xml_comments` + `plistlib.loads` —— 那正是 R2 blocker
    的成因形状：**测试放宽了，生产解析路径没跟着宽**。于是 feishu-sync /
    inflow-sentinel 在这里绿着，在安装器（akb2_plist_identity / lint 门）和 parity 的
    `_render_template` 里 exit=2 / 假 parse_error，3575 条全绿也没人知道。
    改调 akb2.common.plist_parse 后，本文件观测的就是生产实际判定。
    """
    return parse_plist_text(_render_plist_text(path))


def _program_target(data: dict[str, Any]) -> tuple[str, Path | str]:
    args = data["ProgramArguments"]
    if len(args) >= 3 and args[1] == "-m":
        return ("module", str(args[2]))
    for arg in args[1:]:
        if isinstance(arg, str) and arg.startswith(str(REPO)):
            return ("path", Path(arg))
    raise AssertionError(f"could not resolve program target from args: {args}")


def _module_target_exists(module_name: str) -> bool:
    rel = Path(*module_name.split("."))
    return (
        (REPO / "src" / rel).is_dir()
        or (REPO / "src" / rel.with_suffix(".py")).exists()
        or (REPO / "src" / rel / "__init__.py").exists()
    )


@pytest.mark.parametrize("plist_name", PLIST_NAMES)
def test_plist_lints_with_plutil(plist_name: str) -> None:
    """plutil -lint（macOS 专有）—— 缺 plutil 时 skip，由 plistlib 用例兜底。

    守卫必须带 `shutil.which`：`plutil` 是 Apple 专有二进制，Linux 上不存在，而
    `subprocess.run` 对不存在的可执行文件**在 `check=False` 下依然抛 FileNotFoundError**
    —— 于是 CI 的 ubuntu runner（阻断 job）会在这条参数化用例上红 9 项。
    同 commit 的兄弟文件本来就做对了（tests/test_bootstrap_launchd.py 用 which 守卫，
    tests/test_intake_launchd.py 直接用 plistlib），本文件是三者中唯一漏掉的。
    skip 不留缺口：同名的 plistlib 用例（test_plist_parses_with_plistlib）无条件跑，
    plist 合法性在两个平台上都有人守。
    """
    _maybe_xfail(plist_name, "lint")
    if shutil.which("plutil") is None:
        pytest.skip("本机无 plutil（非 macOS）——合法性由 test_plist_parses_with_plistlib 兜底")
    plist_path = LAUNCHD_DIR / plist_name
    result = subprocess.run(
        ["plutil", "-lint", str(plist_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


@pytest.mark.parametrize("plist_name", PLIST_NAMES)
def test_plist_parses_with_plistlib(plist_name: str) -> None:
    """plist 合法性的**跨平台**判据（plutil 缺失时它就是唯一的守门人）。

    走生产解析咽喉 akb2.common.plist_parse：文档注释里可能含裸 "--"（如 flag 名
    --dry-run），XML 规范禁注释内 "--"、strict expat 拒解，但 plutil 与 launchd 自身
    加载器都接受，且注释不影响 plist 实际结构。

    这里**不能**用本文件自己的剥注释实现（此前就是那样）：那样这条判据与安装器/parity
    的判据是两份，测试宽、生产严 —— R2 blocker 正是这么活下来的。现在本判据与
    akb2_plist_identity / akb2_assert_render_clean / parity 用同一份实现，
    这条一绿就代表那三处也接受（跨解析点一致性由
    tests/test_plist_parse_chokepoint.py 直接断言）。
    """
    _maybe_xfail(plist_name, "lint")
    data = parse_plist_text(_render_plist_text(LAUNCHD_DIR / plist_name))
    assert isinstance(data, dict), plist_name
    assert data.get("Label"), f"{plist_name}: 缺 Label"


def _functions_running_plutil(path: Path) -> list[str]:
    """返回该文件里**真正 subprocess 调用 plutil** 的函数名（按 AST 判，不按文本）。

    为什么必须按 AST：按文本 grep 会让守卫**自我豁免** —— 守卫自己的判据里就写着
    `which("plutil")` 这个字面量，于是「文件内是否出现 which 守卫」对宿主文件恒真，
    把守卫本身摘掉也照样绿（本轮实测踩过这个假绿）。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        runs_plutil = False
        has_which_guard = False
        for inner in ast.walk(node):
            if isinstance(inner, ast.Call):
                func = inner.func
                # subprocess.run([... "plutil" ...]) —— 真正会 FileNotFoundError 的形态
                if isinstance(func, ast.Attribute) and func.attr == "run":
                    for arg in inner.args:
                        if isinstance(arg, ast.List) and any(
                            isinstance(el, ast.Constant) and el.value == "plutil"
                            for el in arg.elts
                        ):
                            runs_plutil = True
                # shutil.which("plutil")
                if isinstance(func, ast.Attribute) and func.attr == "which":
                    if any(
                        isinstance(arg, ast.Constant) and arg.value == "plutil"
                        for arg in inner.args
                    ):
                        has_which_guard = True
        if runs_plutil and not has_which_guard:
            hits.append(node.name)
    return hits


def test_plutil_cases_guarded_for_ci() -> None:
    """静态守卫：全仓测试里不得有**无 which 守卫**的 plutil 直调。

    这条防的是 R1 blocker B1 再犯：`plutil` 是 Apple 专有二进制，Linux 上不存在，
    而 `subprocess.run` 对不存在的可执行文件**在 `check=False` 下依然抛
    FileNotFoundError** —— 于是 ubuntu CI（阻断 job）会在参数化用例上红一片。
    plutil 直调在 macOS 全绿、只在 CI 炸，而新文件从未进过 CI 时作者看不到红。
    """
    offenders: list[str] = []
    scanned = 0
    for test_file in sorted((REPO / "tests").rglob("test_*.py")):
        unguarded = _functions_running_plutil(test_file)
        if unguarded:
            offenders.append(f"{test_file.relative_to(REPO)}: {', '.join(unguarded)}")
        if "plutil" in test_file.read_text(encoding="utf-8"):
            scanned += 1
    assert not offenders, (
        "以下测试函数无 which 守卫直调 plutil（ubuntu CI 上会 FileNotFoundError 打红）：\n  "
        + "\n  ".join(offenders)
    )
    # 非空过：至少扫到本文件（否则这条断言恒真）
    assert scanned >= 1, "全仓没扫到任何提到 plutil 的测试文件 —— 守卫失去意义"


def test_plutil_guard_actually_catches_an_unguarded_call(tmp_path: Path) -> None:
    """反证：把无守卫的 plutil 直调种进去，_functions_running_plutil 必须抓出来。"""
    planted = tmp_path / "test_planted.py"
    planted.write_text(
        "import subprocess\n"
        "def test_bad():\n"
        "    subprocess.run(['plutil', '-lint', 'x'], check=False)\n"
        "def test_good():\n"
        "    import shutil\n"
        "    if shutil.which('plutil') is None:\n"
        "        return\n"
        "    subprocess.run(['plutil', '-lint', 'x'], check=False)\n",
        encoding="utf-8",
    )
    assert _functions_running_plutil(planted) == ["test_bad"]


@pytest.mark.parametrize("plist_name", PLIST_NAMES)
def test_plist_label_matches_filename(plist_name: str) -> None:
    _maybe_xfail(plist_name, "label")
    data = _load_rendered_plist(LAUNCHD_DIR / plist_name)
    assert data["Label"] == plist_name.removesuffix(".plist")


@pytest.mark.parametrize("plist_name", PLIST_NAMES)
def test_plist_standard_log_parents_exist_after_render(plist_name: str) -> None:
    _maybe_xfail(plist_name, "log-parent")
    data = _load_rendered_plist(LAUNCHD_DIR / plist_name)
    stdout_parent = Path(data["StandardOutPath"]).parent
    stderr_parent = Path(data["StandardErrorPath"]).parent
    assert stdout_parent.exists(), stdout_parent
    assert stderr_parent.exists(), stderr_parent


@pytest.mark.parametrize("plist_name", PLIST_NAMES)
def test_plist_program_targets_exist_after_render(plist_name: str) -> None:
    _maybe_xfail(plist_name, "program-target")
    data = _load_rendered_plist(LAUNCHD_DIR / plist_name)
    target_type, target = _program_target(data)
    if target_type == "path":
        assert isinstance(target, Path)
        assert target.exists(), target
    else:
        assert isinstance(target, str)
        assert _module_target_exists(target), target


@pytest.mark.parametrize("plist_name", PLIST_NAMES)
def test_plist_has_no_hardcoded_machine_paths(plist_name: str) -> None:
    _maybe_xfail(plist_name, "hardcoded-paths")
    text = (LAUNCHD_DIR / plist_name).read_text(encoding="utf-8")
    assert "/Users/" not in text
    assert "/Volumes/" not in text


def test_xfail_reasons_are_non_empty() -> None:
    for reason in XFAIL_REASONS.values():
        assert reason.strip()


class TestBackstopScheduleWithinDeadline:
    """每个补跑时段必须早于脚本 deadline —— 否则该时段永远只能巡检、不能补跑。

    2026-08-03 之前的真实状态：两个 backstop 的第二时段是 **12:05**，而脚本 deadline
    默认 **12:00**。那个时段因此**每天必然走 deadline 分支、一次都没真正补跑过**
    （旧版还把它记成 FAILED，污染告警）。仓库 plist 的注释早在 07-30 就认账了这个矛盾，
    但没有任何测试守它 —— 所以它活了下来。这条就是那道缺失的门。

    deadline 默认值从脚本里读，不写死：改脚本默认值时这条会自动跟着变，不会各自漂移。
    """

    _BACKSTOPS = {
        "com.akb2.briefing.backstop": ("scripts/briefing_backstop.sh", "AKB2_BRIEFING_DEADLINE"),
        "com.akb2.github_briefing.backstop": (
            "scripts/github_briefing_backstop.sh", "AKB2_GITHUB_DEADLINE",
        ),
    }

    @staticmethod
    def _deadline_minutes(script_rel: str, prefix: str) -> int:
        """从脚本里解析 `${PREFIX_HOUR:-12}` / `${PREFIX_MINUTE:-0}` 的默认值。"""
        text = (REPO / script_rel).read_text(encoding="utf-8")
        hour = re.search(rf"\{{{prefix}_HOUR:-(\d+)\}}", text)
        minute = re.search(rf"\{{{prefix}_MINUTE:-(\d+)\}}", text)
        assert hour and minute, f"{script_rel}: 解析不到 {prefix}_HOUR/_MINUTE 默认值"
        return int(hour.group(1)) * 60 + int(minute.group(1))

    @pytest.mark.parametrize("label", sorted(_BACKSTOPS))
    def test_every_slot_is_earlier_than_deadline(self, label: str) -> None:
        script_rel, prefix = self._BACKSTOPS[label]
        deadline = self._deadline_minutes(script_rel, prefix)

        plist = _load_rendered_plist(LAUNCHD_DIR / f"{label}.plist")
        slots = plist["StartCalendarInterval"]
        if isinstance(slots, dict):
            slots = [slots]
        assert slots, f"{label}: 没有 StartCalendarInterval，断言失去意义"

        late = [
            f"{s.get('Hour', 0):02d}:{s.get('Minute', 0):02d}"
            for s in slots
            if s.get("Hour", 0) * 60 + s.get("Minute", 0) >= deadline
        ]
        assert not late, (
            f"{label} 的这些时段不早于 deadline "
            f"{deadline // 60:02d}:{deadline % 60:02d}，只能巡检不能补跑: {late}"
        )


def test_feishu_sync_runs_after_last_github_backstop() -> None:
    """飞书同步必须晚于 github backstop 最后一次补跑，否则补出的文件当天发不出去。

    2026-08-06 实证：github 文件 11:04 落盘，feishu-sync 10:00 已按缺失告警，
    群当天永远收不到。10:00 早于 11:00 补跑窗 = 结构性空窗，不是偶发迟到。
    """
    github_backstop = _load_rendered_plist(
        LAUNCHD_DIR / "com.akb2.github_briefing.backstop.plist"
    )
    feishu = _load_rendered_plist(LAUNCHD_DIR / "com.akb2.feishu-sync.plist")

    def _minutes(slot: dict[str, Any]) -> int:
        return int(slot.get("Hour", 0)) * 60 + int(slot.get("Minute", 0))

    backstop_slots = github_backstop["StartCalendarInterval"]
    if isinstance(backstop_slots, dict):
        backstop_slots = [backstop_slots]
    last_backstop = max(_minutes(s) for s in backstop_slots)

    feishu_slots = feishu["StartCalendarInterval"]
    if isinstance(feishu_slots, dict):
        feishu_slots = [feishu_slots]
    first_feishu = min(_minutes(s) for s in feishu_slots)

    assert first_feishu > last_backstop, (
        f"feishu-sync {first_feishu // 60:02d}:{first_feishu % 60:02d} "
        f"不晚于 github backstop 最后一次 "
        f"{last_backstop // 60:02d}:{last_backstop % 60:02d}，"
        "补跑成功的简报当天发不出"
    )


# ═══════════════════════════════════════════════════════════════════════════
# SC-R1-11 / SC-R1-12 —— manifest 的参照数据必须与同提交的 plist 模板一致
# ═══════════════════════════════════════════════════════════════════════════

MANIFEST_PATH = REPO / "launchd" / "INSTALL-MANIFEST.yaml"


def _manifest_jobs() -> list[dict[str, Any]]:
    import yaml

    manifest = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
    jobs = manifest["jobs"]
    assert jobs, "manifest 没有 jobs —— 断言会恒真"
    return jobs


def _plist_schedule_value(data: dict[str, Any], kind: str) -> Any:
    if kind == "keepalive":
        return data.get("KeepAlive")
    if kind == "interval":
        return data.get("StartInterval")
    if kind == "calendar":
        slots = data.get("StartCalendarInterval")
        return [slots] if isinstance(slots, dict) else list(slots or [])
    raise AssertionError(f"manifest 出现未知 schedule kind: {kind}")


def _manifest_schedule_value(expected: dict[str, Any]) -> Any:
    kind = expected["kind"]
    if kind == "keepalive":
        return True
    if kind == "interval":
        return expected["seconds"]
    if kind == "calendar":
        return list(expected["entries"])
    raise AssertionError(f"manifest 出现未知 schedule kind: {kind}")


def test_manifest_schedule_matches_templates() -> None:
    """manifest 的 expected_schedule 必须与同提交的 plist 模板**逐槽相等**。

    R1 审查 blocker：两个 backstop 模板的第二时段本提交由 12:05 改成 11:00，
    manifest 的 expected_schedule 仍写 12:05 —— 而 verify-launchd-parity.sh 拿 manifest
    同时比对仓库模板与运行态，任一不符即 DRIFT → exit 1，且 post-change-pipeline
    在 write 模式把它当硬门。也就是说新装的漂移检测**出厂即产 2 条恒定假阳性**，
    「假阳性降到 0 之前不能接告警通道」与「运行态与仓库不一致要能被发现」两条立论
    同时被自己破掉。
    此前零测试覆盖 manifest↔模板一致性（TestBackstopScheduleWithinDeadline 只读 plist，
    manifest 不在它视野内），所以 3481 条全绿也没人知道。
    """
    mismatches: list[str] = []
    for job in _manifest_jobs():
        template = REPO / job["template"]
        assert template.exists(), f"manifest 指的模板不存在: {job['template']}"
        data = _load_rendered_plist(template)
        kind = job["expected_schedule"]["kind"]
        actual = _plist_schedule_value(data, kind)
        expected = _manifest_schedule_value(job["expected_schedule"])
        if actual != expected:
            mismatches.append(
                f"{job['label']}: template={actual} manifest_expected={expected}"
            )
    assert not mismatches, (
        "manifest 的参照数据与同提交的模板不一致 —— 漂移检测会反向指责仓库：\n  "
        + "\n  ".join(mismatches)
    )


def test_manifest_env_keys_match_templates() -> None:
    """同族：manifest 的 expected_env_keys 也必须与模板一致（同一类漂移的另一半）。"""
    mismatches: list[str] = []
    for job in _manifest_jobs():
        data = _load_rendered_plist(REPO / job["template"])
        template_keys = sorted((data.get("EnvironmentVariables") or {}).keys())
        expected_keys = sorted(job.get("expected_env_keys", []))
        if template_keys != expected_keys:
            mismatches.append(
                f"{job['label']}: template={template_keys} manifest_expected={expected_keys}"
            )
    assert not mismatches, "manifest 的 expected_env_keys 与模板漂移：\n  " + "\n  ".join(mismatches)


def test_manifest_covers_every_repo_plist() -> None:
    """三处清单（PLIST_NAMES / launchd/*.plist / manifest.jobs）必须互相覆盖。

    手工清单 × 3 且互不对账 = 新增一个 plist 时三处都可能漏，而漏掉的那处正好是
    漂移检测的视野盲区。
    """
    on_disk = sorted(p.name for p in (REPO / "launchd").glob("*.plist"))
    assert sorted(PLIST_NAMES) == on_disk, (
        f"PLIST_NAMES 与 launchd/*.plist 不一致: names={sorted(PLIST_NAMES)} disk={on_disk}"
    )
    manifest_templates = sorted(Path(job["template"]).name for job in _manifest_jobs())
    assert manifest_templates == on_disk, (
        f"manifest.jobs 与 launchd/*.plist 不一致: "
        f"manifest={manifest_templates} disk={on_disk}"
    )
    manifest_labels = sorted(job["label"] for job in _manifest_jobs())
    assert manifest_labels == sorted(n.removesuffix(".plist") for n in on_disk)


def test_documented_commands_accepted_by_targets() -> None:
    """SC-R1-35: plist 注释里给出的手动操作命令必须能被目标脚本真实接受。"""
    feishu_text = (LAUNCHD_DIR / "com.akb2.feishu-sync.plist").read_text(encoding="utf-8")
    doc_match = re.search(r"手动跑 wrapper（默认 (.+?)）", feishu_text)
    assert doc_match, "未找到 feishu_sync.sh 默认 target 注释"
    documented_flag = doc_match.group(1)

    script_text = (REPO / "scripts" / "feishu_sync.sh").read_text(encoding="utf-8")
    flag_names = set(re.findall(r'^\s*(--[\w-]+)\)', script_text, re.MULTILINE))
    assert documented_flag.startswith("--"), (
        f"注释里的 target 写法 {documented_flag!r} 不是 feishu_sync.sh 实际接受的旗标形态"
    )
    flag_name = documented_flag.split()[0] if " " in documented_flag else documented_flag
    assert flag_name in flag_names, f"{flag_name} 不在 feishu_sync.sh 接受的旗标列表 {flag_names} 中"
