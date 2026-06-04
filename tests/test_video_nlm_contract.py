"""真机 NLM 契约测试 — report create → 轮询 list artifacts → download report。

标记 @integration：需要 NLM 登录态，默认不随 pytest 跑。
手动执行：pytest -m integration tests/test_video_nlm_contract.py -v

验证 SC-2/SC-5：新版 nlm 三步闭环可正确取回报告正文。

自包含：默认创建临时 notebook + text source，跑完三步后删除 notebook（不污染用户数据）。
可通过 AKB2_TEST_NB_ID 复用已有 notebook 跳过创建/删除（更快，但会在该 notebook 留下一份 report）。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time

import pytest

NLM_BIN = os.environ.get("NLM_BIN", "nlm")
NLM_PROFILE = os.environ.get("NLM_PROFILE", "default")
# 复用已有 notebook（可选）；未提供时自动创建临时 notebook
REUSE_NB_ID = os.environ.get("AKB2_TEST_NB_ID")


def _run_nlm(
    *args: str, profile: str = NLM_PROFILE, timeout: int = 60, with_profile: bool = True
) -> str | None:
    """执行 nlm 命令，返回 stdout 或 None（失败/超时）。

    with_profile=False：跳过 --profile（download report 子命令不支持 --profile）。
    """
    cmd = [NLM_BIN] + list(args)
    if with_profile and profile != "alt":
        cmd += ["--profile", profile]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            return None
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        return None


def _extract_uuid(text: str, label: str = "ID") -> str | None:
    """从 nlm 输出提取 UUID（先找 '<label>: <uuid>' 再裸 UUID）。"""
    m = re.search(rf"{label}[:\s]+([0-9a-f-]{{8,}})", text, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    uuid_m = re.search(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        text, re.IGNORECASE,
    )
    return uuid_m.group(0) if uuid_m else None


@pytest.fixture
def contract_notebook(tmp_path):
    """提供一个可生成报告的 notebook ID。

    - REUSE_NB_ID 设置 → 直接复用，不创建不删除
    - 否则 → 创建临时 notebook + text source（--wait），yield 后删除
    """
    if REUSE_NB_ID:
        yield REUSE_NB_ID
        return

    create_out = _run_nlm(
        "notebook", "create", "[AKB2 Contract Test] nlm 三步契约",
        timeout=60,
    )
    if not create_out:
        pytest.skip("无法创建测试 notebook（NLM 未登录或配额耗尽）")
    nb_id = _extract_uuid(create_out)
    if not nb_id:
        pytest.skip(f"无法解析 notebook ID: {create_out[:200]}")

    # 加一个 text source 供报告生成（--wait 等处理完成）
    src_out = _run_nlm(
        "source", "add", nb_id,
        "--text",
        "DeepSeek V4 是一个采用 MoE 架构的大语言模型，"
        "在 MMLU 基准上达到 92.5%，支持 128K 上下文窗口。"
        "本测试用于验证 nlm 报告生成三步契约。",
        "--wait",
        timeout=180,
    )
    if src_out is None:
        _run_nlm("notebook", "delete", nb_id, "--confirm", timeout=60)
        pytest.skip("无法添加 text source")

    try:
        yield nb_id
    finally:
        _run_nlm("notebook", "delete", nb_id, "--confirm", timeout=60)


@pytest.mark.integration
def test_nlm_report_create_poll_download(contract_notebook, tmp_path):
    """真机契约：report create(异步)→轮询 list artifacts(status)→download report 三步闭环。

    验证：
    - report create 返回 Artifact ID
    - list artifacts --json 最终报告 status=completed
    - download report 写出非空 markdown 文件
    """
    nb_id = contract_notebook

    # Step 1: report create（异步）
    create_output = _run_nlm(
        "report", "create", nb_id,
        "--format", "Briefing Doc",
        "--language", "zh-Hans",
        "--confirm",
        timeout=60,
    )
    assert create_output is not None, "report create 失败或超时"

    artifact_id = _extract_uuid(create_output, label="Artifact ID")
    assert artifact_id, f"无法从 report create 输出提取 Artifact ID: {create_output[:300]}"

    # Step 2: 轮询 list artifacts --json 等待 completed（最多 15 分钟）
    max_wait = 900
    poll_interval = 15
    elapsed = 0
    completed = False
    while elapsed < max_wait:
        list_output = _run_nlm("list", "artifacts", nb_id, "--json", timeout=60)
        if list_output:
            try:
                artifacts = json.loads(list_output)
                for art in artifacts:
                    if art.get("id") == artifact_id and art.get("status") == "completed":
                        completed = True
                        break
            except (json.JSONDecodeError, TypeError):
                pass
        if completed:
            break
        time.sleep(poll_interval)
        elapsed += poll_interval

    assert completed, f"report 轮询超时 ({max_wait}s)，artifact_id={artifact_id}"

    # Step 3: download report
    report_path = tmp_path / f"contract-test-{artifact_id[:8]}.md"
    dl_output = _run_nlm(
        "download", "report", nb_id,
        "--id", artifact_id,
        "-o", str(report_path),
        with_profile=False,  # download report 不支持 --profile
        timeout=120,
    )
    assert dl_output is not None, f"download report 失败，artifact_id={artifact_id}"
    assert report_path.exists(), f"报告文件未生成: {report_path}"

    content = report_path.read_text(encoding="utf-8")
    assert len(content) > 100, f"报告内容过短（{len(content)} 字符），可能下载失败"
    assert "#" in content, "报告内容应为 markdown 格式"
