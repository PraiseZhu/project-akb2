"""test_translate_fidelity_sample.py — 译前/译后配对样本落盘。

为什么这个文件存在：audit 的 4 个 fail 级确定性检查（number_mismatch / url_missing /
handle_missing / metric_fabricated）都需要「英文原文 ↔ 中文译文」配对才能判定，而
vault 只有中文成品 → 没有配对语料时误报率**在原理上**不可测。translate 是唯一同时
持有两侧的阶段，所以"样本是否真的落盘"是整条测量链的地基。

绑定生产路径：所有断言都从 `translate_briefing()` 入口进（只 mock
`synthesis._call_llm` 这个真实网关边界），不直接调 `dump_fidelity_pairs`。
本仓栽过三次"测试里手工传参、生产从不传"的坑（test_L1_url_dedup 的 db= / mark_seen
漏传 first_mention_at / display_slug 生产零调用），故不再用内部函数做主断言。
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from akb2.briefing import translate as T
from akb2.common.state_paths import is_synced_path
from tests.briefing.test_translate import (
    FIXTURE_SYNTH,
    MOCK_TRANSLATE_RESPONSE,
    _make_mock_call_llm,
)


def _run_translate(synth=None, response=None) -> dict:
    """走生产入口跑一次翻译（唯一 mock 点 = LLM 网关）。"""
    with patch(
        "akb2.briefing.synthesis._call_llm",
        side_effect=_make_mock_call_llm(response or MOCK_TRANSLATE_RESPONSE),
    ):
        return T.translate_briefing(synth or FIXTURE_SYNTH)


def _read_samples() -> list[dict]:
    sample_dir = T.fidelity_sample_dir()
    records: list[dict] = []
    for path in sorted(sample_dir.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))
    return records


# ---------------------------------------------------------------------------
# 样本存在性：这是解锁测量的前提
# ---------------------------------------------------------------------------

def test_translate_briefing_writes_paired_samples():
    """生产入口跑完一次翻译后，配对语料必须真的在磁盘上。"""
    result = _run_translate()

    records = _read_samples()
    assert records, "translate_briefing 跑完后没有任何配对样本落盘"

    fields = {r["field"] for r in records}
    assert "executive_summary" in fields, fields
    assert any(f.startswith("focus_discussions[") for f in fields), fields
    assert any(f.startswith("tweets[") for f in fields), fields

    exec_rec = next(r for r in records if r["field"] == "executive_summary")
    assert exec_rec["en"] == FIXTURE_SYNTH["executive_summary"]
    assert exec_rec["zh"] == result["executive_summary"]
    assert exec_rec["en"] != exec_rec["zh"], "en/zh 相同的配对不该入语料（恒等回放陷阱）"
    assert exec_rec["date"] == FIXTURE_SYNTH["date"]
    # 只存审计所需字段，不夹带整份 payload
    assert set(exec_rec) == {"date", "field", "en", "zh", "captured_at"}, exec_rec


def test_untranslated_item_is_not_written_as_an_identity_pair():
    """单条推文翻译失败（译文仍是英文原文）→ 该 en == zh 伪配对不得进语料。

    这是"恒等回放同义反复"的入口防线：en == zh 的配对上，数字/URL/@handle 在构造上
    必然逐字保留，判据不可能报错，留在语料里只会把误报率分母兑水、把结论稀释成
    "看起来很低"。整体失败路径另有 `test_translate_failed_path_writes_no_sample`
    覆盖，本例覆盖的是**部分失败**（更常见）。
    """
    partial = dict(MOCK_TRANSLATE_RESPONSE)
    # 只保留 idx=0 的推文条目：idx=1 未预置 → mock 返回空对象 → 该条被拒收，
    # 译文保持英文原文（走生产降级路径，不是测试里手工造的状态）
    partial["tweets"] = [t for t in MOCK_TRANSLATE_RESPONSE["tweets"] if t["idx"] == 0]

    result = _run_translate(response=partial)
    assert "tweets[1]" in result["translate_failed_fields"], result.get(
        "translate_failed_fields"
    )
    assert result["tweets"][1]["text"] == FIXTURE_SYNTH["tweets"][1]["text"], "前提不成立"

    records = _read_samples()
    assert records, "部分失败不应导致整批不落盘"
    for rec in records:
        assert rec["en"] != rec["zh"], rec
    assert not any(r["field"] == "tweets[1].text" for r in records), records


def test_sample_file_named_by_briefing_date_and_appends():
    """同一天两次运行追加到同一文件（不互相覆盖，语料才能攒起来）。"""
    _run_translate()
    first = len(_read_samples())
    _run_translate()
    second = len(_read_samples())

    assert second == 2 * first, (first, second)
    files = sorted(p.name for p in T.fidelity_sample_dir().glob("*.jsonl"))
    assert files == [f"{FIXTURE_SYNTH['date']}.jsonl"], files


def test_sample_path_is_outside_cross_machine_sync():
    """落盘路径不得在跨机同步范围内（Syncthing 会把状态文件分叉，已实证丢数据）。"""
    _run_translate()
    sample_dir = T.fidelity_sample_dir()
    assert not is_synced_path(sample_dir), sample_dir
    for path in sample_dir.glob("*.jsonl"):
        assert not is_synced_path(path), path


def test_sample_never_written_into_vault(tmp_path, monkeypatch):
    """vault 只读（宪法原则 5）：VAULT_ROOT 下不得出现任何采样文件。"""
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setenv("VAULT_ROOT", str(vault))
    _run_translate()
    assert list(vault.rglob("*")) == [], "采样写进了 vault"
    assert _read_samples(), "状态根侧仍应有样本"


# ---------------------------------------------------------------------------
# 预算（宪法原则 1：显式停止条件）
# ---------------------------------------------------------------------------

def test_per_run_pair_budget_stops_writing_and_is_visible(monkeypatch, capsys):
    """超过单次条数上限即停止落盘，且必须有可见记录（不静默截断）。"""
    monkeypatch.setattr(T, "FIDELITY_SAMPLE_MAX_PAIRS_PER_RUN", 2)
    result = _run_translate()

    records = _read_samples()
    assert len(records) == 2, records
    assert result["fidelity_sample_truncated"] >= 1, result.get("fidelity_sample_truncated")
    err = capsys.readouterr().err
    assert "配对样本触达单次上限" in err, err


def test_dir_byte_budget_stops_writing_and_is_visible(monkeypatch, capsys):
    """目录字节上限触达 → 停写 + 可见告警 + 结果打标记（不静默膨胀）。"""
    sample_dir = T.fidelity_sample_dir()
    sample_dir.mkdir(parents=True, exist_ok=True)
    (sample_dir / "2020-01-01.jsonl").write_text("x" * 2048, encoding="utf-8")
    monkeypatch.setattr(T, "FIDELITY_SAMPLE_MAX_DIR_BYTES", 1024)

    result = _run_translate()

    assert result["fidelity_sample_failed"] == "dir_budget", result.get(
        "fidelity_sample_failed"
    )
    assert not (sample_dir / f"{FIXTURE_SYNTH['date']}.jsonl").exists()
    err = capsys.readouterr().err
    assert "配对样本目录已用" in err, err


def test_retention_days_is_a_named_budget():
    """保留天数是具名常量（评测侧据此排除过期样本），不是散落字面量。"""
    assert isinstance(T.FIDELITY_SAMPLE_RETENTION_DAYS, int)
    assert T.FIDELITY_SAMPLE_RETENTION_DAYS > 0


# ---------------------------------------------------------------------------
# 开关与失败可见
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", ["0", "false", "no", "off", "OFF"])
def test_sampling_can_be_disabled(monkeypatch, value):
    monkeypatch.setenv(T.FIDELITY_SAMPLE_ENV, value)
    _run_translate()
    assert _read_samples() == []


def test_sampling_on_by_default(monkeypatch):
    """未设环境变量时默认开启（否则"误报率不可测"这条风险永远解不开）。"""
    monkeypatch.delenv(T.FIDELITY_SAMPLE_ENV, raising=False)
    _run_translate()
    assert _read_samples()


def test_dump_failure_is_visible_and_never_breaks_translation(monkeypatch, capsys):
    """落盘失败：告警 + result 打标记，但译文照常返回（采样不得打挂日报）。"""
    def boom(*_a, **_kw):
        raise OSError("disk on fire")

    monkeypatch.setattr(Path, "mkdir", boom)
    result = _run_translate()

    assert result["executive_summary"] == MOCK_TRANSLATE_RESPONSE["executive_summary"]
    assert "OSError" in result["fidelity_sample_failed"], result.get(
        "fidelity_sample_failed"
    )
    err = capsys.readouterr().err
    assert "配对样本落盘失败" in err, err


def test_translate_failed_path_writes_no_sample(capsys):
    """全模型失败时产出仍是英文（en == zh），不得把这种伪配对写进语料。"""
    def all_fail(*_a, **_kw):
        raise RuntimeError("gateway down")

    with patch("akb2.briefing.synthesis._call_llm", side_effect=all_fail):
        result = T.translate_briefing(FIXTURE_SYNTH)

    assert result["translate_failed"] is True
    assert _read_samples() == []
    capsys.readouterr()
