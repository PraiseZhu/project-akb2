"""SC-R2-16 —— launchd/INSTALL-MANIFEST.yaml 里每条抑制项都必须带"为什么抑制"。

抑制项（`ignore_installed_env_keys`）的语义是：**明知运行态与仓库模板不一致、决定不报**。
它是漂移检测唯一的合法例外口。此前 schema 是裸字符串列表：

    ignore_installed_env_keys:
      - AKB2_TRANSLATE_TIMEOUT

于是「为什么抑制」不在任何地方 —— 半年后没人知道它当初是"已确认无害"还是"当时来不及查"，
删也不敢删（万一是前者，删了就凭空多一条恒定假阳性；万一是后者，留着就永久掩盖一条真漂移）。
无理由的抑制 = 永久债。

现在 schema 是 `{key, reason}`，理由与键**物理不可分离**：数据层面就不存在"加了抑制却
没写为什么"这种状态，不靠人记得去补。

本文件守数据侧（每条都有非空理由、理由要有信息量）；
gate 侧的 fail-closed 行为（缺 reason → parity 非零退出）在
tests/test_verify_launchd_parity.py::test_parity_rejects_suppression_without_reason。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPO / "launchd" / "INSTALL-MANIFEST.yaml"
PARITY_SH = REPO / "scripts" / "verify-launchd-parity.sh"

# 理由的最低信息量：短于此的基本是"暂不处理""无影响"这类零信息占位。
MIN_REASON_CHARS = 30

# 禁用词：只描述"决定"而不给依据的说法。命中即视为没写理由。
# （与全局规则「痛点列禁用词」同一逻辑：不能只说"更好/无影响"，要说可观测的事实。）
_EMPTY_REASON_PATTERNS = (
    r"^\s*(暂不处理|暂时忽略|无影响|不影响|忽略|TODO|待查|N/?A|见上|同上)\s*[。.]?\s*$",
)


def _manifest() -> dict[str, Any]:
    data = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, dict) and data.get("jobs"), "manifest 结构异常，断言会恒真"
    return data


def _suppression_entries() -> list[tuple[str, Any]]:
    """返回 [(label, entry), ...]，entry 原样不做归一化（schema 违规也要能被看见）。"""
    found: list[tuple[str, Any]] = []
    for job in _manifest()["jobs"]:
        for entry in job.get("ignore_installed_env_keys") or []:
            found.append((job["label"], entry))
    return found


def test_every_ignore_entry_has_reason() -> None:
    """每条 ignore_installed_env_keys 条目都是 {key, reason} 且 reason 非空、有信息量。"""
    entries = _suppression_entries()
    # 非空过：manifest 里一条抑制都没有时本断言恒真 —— 那就该由下面这条提醒去掉本文件，
    # 而不是让一个永远绿的守卫留在仓里假装有覆盖。
    assert entries, (
        "manifest 里已没有任何 ignore_installed_env_keys —— 本守卫恒真，"
        "请确认是真的清空了抑制（那样可以删掉本文件）还是 schema 变了"
    )

    problems: list[str] = []
    for label, entry in entries:
        if not isinstance(entry, dict):
            problems.append(
                f"{label}: 条目不是 {{key, reason}} 映射（裸字符串 = 没有理由的抑制）: {entry!r}"
            )
            continue
        unknown = set(entry) - {"key", "reason"}
        if unknown:
            problems.append(f"{label}: 条目有未知字段 {sorted(unknown)}")
        key = str(entry.get("key", "")).strip()
        reason = str(entry.get("reason", "")).strip()
        if not key:
            problems.append(f"{label}: 条目缺 key: {entry!r}")
        if not reason:
            problems.append(f"{label}: 抑制 {key or entry!r} 的 reason 为空")
            continue
        if len(reason) < MIN_REASON_CHARS:
            problems.append(
                f"{label}: 抑制 {key} 的 reason 只有 {len(reason)} 字，"
                f"说不清为什么可以不报: {reason!r}"
            )
        if any(re.match(p, reason) for p in _EMPTY_REASON_PATTERNS):
            problems.append(
                f"{label}: 抑制 {key} 的 reason 只表达了决定、没给依据: {reason!r}"
            )

    assert not problems, (
        "以下抑制项没写清「为什么抑制」（无理由的抑制下次没人敢删，成永久债）：\n  "
        + "\n  ".join(problems)
    )


def test_suppressed_keys_are_actually_absent_from_repo_template() -> None:
    """抑制只针对**运行态多出来的**键：模板里已有的键不该被抑制。

    如果模板和运行态都有这个键，那它本来就该逐值比对；把它抑制掉等于让一条真实的
    值漂移永久隐身。这条把抑制的适用范围钉在"仅 installed 侧多出"上。
    """
    from akb2.common.plist_parse import parse_plist_file

    misuse: list[str] = []
    by_label = {job["label"]: job for job in _manifest()["jobs"]}
    for label, entry in _suppression_entries():
        if not isinstance(entry, dict):
            continue                      # schema 违规由上一条断言报
        key = str(entry.get("key", "")).strip()
        template = REPO / by_label[label]["template"]
        env = parse_plist_file(template).get("EnvironmentVariables") or {}
        if key in env:
            misuse.append(
                f"{label}: 抑制了模板里本来就有的 {key} —— 该键的值漂移会永久隐身"
            )
    assert not misuse, "抑制被用在了不该用的地方：\n  " + "\n  ".join(misuse)


def test_parity_parses_suppressions_through_a_single_owner() -> None:
    """parity 侧只有一处解析抑制项（`_ignore_env_keys`），且它强制 reason 非空。

    结构断言：两处各自解析 = 两份 schema 判据，其中一处忘了校验 reason 就等于没校验。
    行为断言在 tests/test_verify_launchd_parity.py::
    test_parity_rejects_suppression_without_reason。
    """
    code = [
        line.split("#", 1)[0]
        for line in PARITY_SH.read_text(encoding="utf-8").splitlines()
    ]
    # 判据钉在「从 manifest 里**取**这个字段」上（`job.get("ignore_installed_env_keys")`），
    # 不是"提到过这个词"：文档串与错误消息里都会提它，按词计数会把注释也算成解析点。
    readers = [
        f"{lineno}: {line.strip()}"
        for lineno, line in enumerate(code, 1)
        if re.search(r'\.get\(\s*["\']ignore_installed_env_keys["\']', line)
    ]
    assert len(readers) == 1, (
        "manifest 的 ignore_installed_env_keys 不是只有一处解析口 —— 两处各自解析就是"
        "两份 schema 判据，漏校验 reason 的那处等于没校验：\n  " + "\n  ".join(readers)
    )
    assert "_ignore_env_keys" in "\n".join(code), "抑制项解析没有具名的单一 owner"
    assert "没写 reason" in PARITY_SH.read_text(encoding="utf-8"), (
        "解析口没有对缺 reason 的 fail-closed 分支"
    )
