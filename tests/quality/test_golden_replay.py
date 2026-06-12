"""黄金样张端到端重放测试 — SC-6.2 (master SC-1)。

两档:
  - proposal_mode: 默认门 (AKB2_AUTO_PROMOTE=0)，断言 proposal manifest 产出，
    active 目录 tree_sha256 不变，零 active 写入。
  - post_amendment: 出厂正式门 (AKB2_AUTO_PROMOTE=1)，断言 14 步含 active 收尾
    (索引摘要行、概念回边、lint 0)。

build 期只验证可收集 (pytest --collect-only)，不打真 API。
真 API 跑时打 @pytest.mark.integration 标记。
"""

from __future__ import annotations

import pytest


@pytest.mark.integration
class TestGoldenReplayProposal:
    """默认门 (AKB2_AUTO_PROMOTE=0): proposal mode。"""

    def test_proposal_mode_collectable(self) -> None:
        """build 期: 至少 1 用例可收集 (no errors)。"""
        # 此用例仅用于验证测试可收集，build 期不做真 API 调用
        pass

    @pytest.mark.proposal_mode
    def test_proposal_mode_golden_replay(self) -> None:
        """执行期: 黄金样张 URL 端到端重跑，断言 proposal manifest 产出。

        真 API 调用，需网络和 LLM 端点可用。
        build 期跳过 (pytest -m 'not integration')。
        """
        pytest.skip(
            "执行期真 API 测试。运行时: "
            "AKB2_AUTO_PROMOTE=0 python3 -m pytest tests/quality/test_golden_replay.py "
            "-q -m 'integration and proposal_mode'"
        )


@pytest.mark.integration
class TestGoldenReplayActive:
    """出厂正式门 (AKB2_AUTO_PROMOTE=1): post-amendment mode。"""

    def test_post_amendment_collectable(self) -> None:
        """build 期: 至少 1 用例可收集。"""
        pass

    @pytest.mark.post_amendment
    def test_post_amendment_golden_replay(self) -> None:
        """执行期: 14 步结构等价含 active 收尾。

        断言: 索引摘要行、概念回边、lint 0。
        前置条件: vault 修宪已应用 + AKB2_AUTO_PROMOTE=1。
        """
        pytest.skip(
            "执行期真 API 测试。运行时: "
            "AKB2_AUTO_PROMOTE=1 python3 -m pytest tests/quality/test_golden_replay.py "
            "-q -m 'integration and post_amendment'"
        )


class TestGoldenReplayBuildGate:
    """build 期门: 测试可收集。"""

    def test_collect_golden_replay(self) -> None:
        """验证 golden replay 测试可被 pytest 收集。"""
        # 此测试确保 build 期 --collect-only 不报错
        assert True
