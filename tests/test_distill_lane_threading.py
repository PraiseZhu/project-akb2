"""test_distill_lane_threading.py — step-1 content_lane 穿过 distill/抽取子进程。

覆盖 SC-5 / SC-8:
    - SC-5: content_lane 出现在 candidate frontmatter
    - SC-8: content_lane 真正穿过 extract 子进程边界
        * worker_payload_carries_lane: _run_extract_claims_worker 传携 lane 的 JSON payload
        * worker_main_forwards_lane: distill_worker.main() 读 JSON → extract_claims(content_lane=)
        * extract_funcs_accept_lane: extract_claims / build_deterministic / _validate_extract_output
          均接 content_lane 形参
"""

from __future__ import annotations

import inspect
import io
import json

import pytest

from src.akb2.quality import distill, distill_worker

# ---------------------------------------------------------------------------
# SC-5: content_lane 进 candidate frontmatter
# ---------------------------------------------------------------------------


class TestFrontmatterCarriesLane:
    def test_frontmatter_carries_lane(self) -> None:
        """_build_frontmatter 写入 content_lane 键，值为透传车道。"""
        extract_data = {
            "claims": [
                {
                    "text": "Loop engineering designs bounded convergent loops",
                    "anchor": "§A",
                    "provenance": "source_grounded",
                    "evidence_gap": "未量化：原文未给可度量判据",
                }
            ],
            "referenced_concepts": ["loop-engineering", "harness-engineering"],
            "how_steps": [],
        }
        fm = distill._build_frontmatter(
            "raw/concept--raw--2026-06-26.md",
            extract_data,
            content_lane="concept",
        )
        assert "content_lane" in fm, "candidate frontmatter 应含 content_lane 键"
        assert fm["content_lane"] == "concept"

    def test_frontmatter_lane_defaults_empty(self) -> None:
        """不传 content_lane 时键存在且为空（旧调用兼容、键稳定）。"""
        extract_data = {
            "claims": [
                {
                    "text": "x",
                    "anchor": "§A",
                    "provenance": "source_grounded",
                    "evidence_gap": "未提供",
                }
            ],
            "referenced_concepts": ["a", "b"],
            "how_steps": [],
        }
        fm = distill._build_frontmatter("raw/x--raw--2026-06-26.md", extract_data)
        assert fm["content_lane"] == ""


# ---------------------------------------------------------------------------
# SC-8: 抽取子进程过界
# ---------------------------------------------------------------------------


class TestExtractFuncsAcceptLane:
    def test_extract_funcs_accept_lane(self) -> None:
        """extract_claims / build_deterministic / _validate_extract_output 接 content_lane。"""
        assert "content_lane" in inspect.signature(distill.extract_claims).parameters
        assert (
            "content_lane"
            in inspect.signature(distill.build_deterministic_extract_result).parameters
        )
        assert (
            "content_lane"
            in inspect.signature(distill._validate_extract_output).parameters
        )

    def test_validate_extract_output_lane_behavior_unchanged(self) -> None:
        """_validate_extract_output 接 content_lane；§0.2/A2 后 concept 不再 extract 判 F4。

        本 step 不改 claims 空校验：concept lane 与默认对同一非法数据（claims 空）应抛
        同样的 ValueError。F4（mechanism≥3/连边）已 A2 删前移，deferred to integrity_floor
        （见 test_integrity_floor TestCoreValueF4）。
        """
        bad_data: dict = {"claims": []}  # claims 为空 → 必抛 ValueError
        with pytest.raises(ValueError):
            distill._validate_extract_output(bad_data, content_lane="concept")
        with pytest.raises(ValueError):
            distill._validate_extract_output(bad_data, content_lane="")


class TestWorkerMainForwardsLane:
    def test_worker_main_forwards_lane(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """distill_worker.main() 读 JSON payload → 转 extract_claims(content_lane=...)。"""
        captured: dict = {}

        class _FakeResult:
            success = True
            data = {"ok": 1}
            retries_used = 0
            error = ""

        def _fake_extract(raw_content, content_lane=""):
            captured["raw_content"] = raw_content
            captured["content_lane"] = content_lane
            return _FakeResult()

        monkeypatch.setattr(distill_worker, "extract_claims", _fake_extract)
        payload = json.dumps(
            {"raw_content": "some raw body", "content_lane": "concept"},
            ensure_ascii=False,
        )
        monkeypatch.setattr("sys.stdin", io.StringIO(payload))

        rc = distill_worker.main()
        assert rc == 0
        assert captured["raw_content"] == "some raw body"
        assert captured["content_lane"] == "concept", "worker 必须把 JSON 中的 lane 转给 extract_claims"

    def test_worker_main_backward_compat_json_string(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """向后兼容：JSON 字符串 stdin（json.dumps(raw)）视为 raw_content、lane=''。"""
        captured: dict = {}

        class _FakeResult:
            success = True
            data = {}
            retries_used = 0
            error = ""

        def _fake_extract(raw_content, content_lane=""):
            captured["raw_content"] = raw_content
            captured["content_lane"] = content_lane
            return _FakeResult()

        monkeypatch.setattr(distill_worker, "extract_claims", _fake_extract)
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps("legacy raw text")))

        rc = distill_worker.main()
        assert rc == 0
        assert captured["raw_content"] == "legacy raw text"
        assert captured["content_lane"] == ""


class TestWorkerPayloadCarriesLane:
    def test_worker_payload_carries_lane(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_run_extract_claims_worker 向子进程传携 content_lane 的 JSON payload（非裸 raw）。"""
        from src.akb2.quality import runner

        captured: dict = {}

        class _FakeProc:
            returncode = 0
            pid = 99999

            def communicate(self, input=None, timeout=None):
                captured["input"] = input
                # 返回合法 worker payload
                return (
                    json.dumps(
                        {"success": True, "data": {}, "retries_used": 0, "error": ""}
                    ),
                    "",
                )

            def poll(self):
                return 0

        def _fake_popen(cmd, **kwargs):
            captured["cmd"] = cmd
            return _FakeProc()

        monkeypatch.setattr(runner.subprocess, "Popen", _fake_popen)

        runner._run_extract_claims_worker("RAW BODY HERE", content_lane="procedural")

        assert "input" in captured, "worker 应通过 stdin 传 payload"
        # 传的是 JSON payload（非裸 raw_content）
        payload = json.loads(captured["input"])
        assert isinstance(payload, dict)
        assert payload["raw_content"] == "RAW BODY HERE"
        assert payload["content_lane"] == "procedural", "payload 必须携 content_lane 穿过子进程边界"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
