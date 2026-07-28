"""test_pipeline_degradation.py — social 简报的降级可见性 / 拒收 / 跨日去重 / gate 复跑。

覆盖缺陷：
  #1  intake 降级到 fixture（测试样例数据）时必须硬阻断，绝不写入 Social
  #4  audit verdict=fail 不发布、warn 强制降级标记
  #9  synthesis / translate / intake 三类降级状态必须落盘 frontmatter
  #6  跨日去重账本（进程外持久）
  #16 gate 在最终写入的那份文件上复跑（三重断言真正成立）
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# 素材
# ---------------------------------------------------------------------------

DATE = "2026-05-30"


def _tweet(tid: str, author: str = "@sama", score: float = 0.9) -> dict:
    return {
        "id": tid,
        "author": author,
        "text": f"tweet body {tid} about AI agents",
        "created_at": f"{DATE}T02:00:00Z",
        "score": score,
        "dimensions": {"novelty": 1.0},
        "url": f"https://x.com/{tid}",
    }


def _synth(tweets: list[dict], *, source_name: str = "deepseek_synthesis") -> dict:
    return {
        "date": DATE,
        "generated_at": f"{DATE}T06:00:00Z",
        "source": {"name": source_name, "collector_version": "1.0.0"},
        "executive_summary": "今日 AI 智能体进展综述。",
        "scoring_summary": {"total_tweets": len(tweets), "scored_tweets": len(tweets)},
        "tweets": tweets,
        "focus_discussions": [
            {
                "topic": "智能体记忆",
                "summary": "多款记忆工具涌现。",
                "supporting_tweet_ids": [t["id"] for t in tweets],
            }
        ],
    }


PASS_VERDICT = {
    "verdict": "pass",
    "standard": "pass",
    "standard_issues": [],
    "standard_issues_structured": [],
    "llm_audit": "pass",
    "audit_degraded": False,
    "drift_score": 0.05,
    "accuracy_issues": [],
    "fix_loop_reason": "converged",
    "ts": "",
}


def _vault(tmp_path: Path) -> Path:
    (tmp_path / "Praise Space" / "AKB2.0" / "staging" / "candidate").mkdir(parents=True)
    (tmp_path / "Praise Space" / "AKB2.0" / "AI TRENDS" / "Social").mkdir(parents=True)
    return tmp_path


def _social(tmp_path: Path) -> Path:
    return (
        tmp_path / "Praise Space" / "AKB2.0" / "AI TRENDS" / "Social"
        / f"{DATE} Daily Briefing.md"
    )


def _staging(tmp_path: Path) -> Path:
    return (
        tmp_path / "Praise Space" / "AKB2.0" / "staging" / "candidate"
        / f"{DATE} Daily Briefing.md"
    )


def _frontmatter(text: str) -> str:
    parts = text.split("---", 2)
    return parts[1] if len(parts) >= 3 else ""


# ---------------------------------------------------------------------------
# #1 fixture 降级硬阻断
# ---------------------------------------------------------------------------

def test_intake_fixture_fallback_blocks_social(tmp_path, monkeypatch, capsys):
    """intake 标 _fallback_used（降级到测试 fixture）→ Social 无文件、退出码非 0、
    昂贵阶段（synthesis）不启动、降级原因落盘诊断 JSON。"""
    from akb2.briefing import pipeline as pl

    vault_root = _vault(tmp_path)
    diag_dir = tmp_path / "diag"
    diag_dir.mkdir()
    monkeypatch.setenv("AKB2_BRIEFING_DIAG_DIR", str(diag_dir))

    fallback_intake = {"tweets": [_tweet("19250010000000000AA")], "_fallback_used": True}

    with (
        patch.object(pl, "run_intake", return_value=fallback_intake),
        patch.object(pl, "run_synthesis") as mock_synth,
        patch.object(pl, "run_gate") as mock_gate,
    ):
        with pytest.raises(SystemExit) as exc:
            pl.main(["--date", DATE, "--vault-root", str(vault_root)])

    assert exc.value.code != 0, "fixture 降级必须以非 0 退出码结束"
    assert not _social(tmp_path).exists(), "fixture 降级时 Social 绝不能有文件"
    assert mock_synth.call_count == 0, "阻断应发生在 synthesis 之前"
    assert mock_gate.call_count == 0, "阻断应发生在 gate 之前"

    diag_files = list(diag_dir.glob("briefing-social-*.json"))
    assert diag_files, "降级原因必须落盘到诊断 JSON"
    diag = json.loads(diag_files[0].read_text())
    assert "fixture" in diag["reason"], f"诊断应说明 fixture 降级，实际 {diag['reason']}"
    assert diag["date"] == DATE


def test_intake_fixture_content_blocks_even_without_flag(tmp_path):
    """即使 _fallback_used 标记丢失，产出内容全是 fixture 推文 id 时也必须阻断。"""
    from akb2.briefing import pipeline as pl
    from akb2.briefing.intake.l0_collector import FIXTURE_PATH

    vault_root = _vault(tmp_path)
    fixture_tweets = json.loads(Path(FIXTURE_PATH).read_text())["tweets"]

    with (
        patch.object(pl, "run_intake", return_value={"tweets": fixture_tweets}),
        patch.object(pl, "run_synthesis") as mock_synth,
    ):
        with pytest.raises(SystemExit) as exc:
            pl.main(["--date", DATE, "--vault-root", str(vault_root)])

    assert exc.value.code != 0
    assert not _social(tmp_path).exists(), "fixture 内容不得进入 Social"
    assert mock_synth.call_count == 0


def test_run_intake_surfaces_subprocess_stderr_on_success(tmp_path, capsys):
    """collector exit 0 但 stderr 有 WARNING（降级提示）时，warning 必须出现在本进程 stderr。"""
    from akb2.briefing import pipeline as pl

    out_path = tmp_path / "intake.json"

    class _Result:
        returncode = 0
        stdout = ""
        stderr = "WARNING: curator fetch_tier.en returned 0 tweets — fallback to fixture\n"

    def fake_run(cmd, **kwargs):
        out_path.write_text('{"tweets": [], "_fallback_used": true}')
        return _Result()

    with patch("akb2.briefing.pipeline.subprocess.run", side_effect=fake_run):
        data = pl.run_intake(dry_run=False, output_path=out_path)

    err = capsys.readouterr().err
    assert "fallback to fixture" in err, "子进程 warning 被吞掉了（缺陷 #1 根因之一）"
    assert data["_fallback_used"] is True


# ---------------------------------------------------------------------------
# #9 降级标记落盘
# ---------------------------------------------------------------------------

def _run_pipeline(
    tmp_path,
    *,
    tweets=None,
    synth_source="deepseek_synthesis",
    translate=None,
    verdict=None,
    gate="active",
    argv_extra=(),
):
    """跑一遍 pipeline（上游全部替身），返回 (result_or_exit, gate_calls, translate_inputs)。"""
    from akb2.briefing import pipeline as pl

    tweets = tweets if tweets is not None else [_tweet("100"), _tweet("200", "@ylecun", 0.8)]
    gate_calls: list[dict] = []
    translate_inputs: list[dict] = []

    def mock_gate(*, akb_root, input_file, date):
        gate_calls.append({"input_file": Path(input_file), "text": Path(input_file).read_text()})
        return gate(len(gate_calls)) if callable(gate) else gate

    def default_translate(synth, **kwargs):
        return json.loads(json.dumps(synth))  # 深拷贝：非同一对象 = 翻译成功

    def mock_synthesis(scored, date):
        return _synth(scored.get("tweets", []), source_name=synth_source)

    def mock_fix_loop(zh, en, **kwargs):
        return zh, dict(verdict or PASS_VERDICT)

    translate_fn = translate or default_translate

    def wrapped_translate(synth, **kwargs):
        translate_inputs.append(synth)
        return translate_fn(synth, **kwargs)

    argv = ["--date", DATE, "--vault-root", str(tmp_path), *argv_extra]
    with (
        patch.object(pl, "run_intake", return_value={"tweets": tweets}),
        patch.object(pl, "run_synthesis", side_effect=mock_synthesis),
        patch.object(pl, "run_gate", side_effect=mock_gate),
        patch("akb2.briefing.pipeline.translate_briefing", side_effect=wrapped_translate),
        patch.object(pl, "run_audit_fix_loop", side_effect=mock_fix_loop),
    ):
        outcome = pl.main(argv)
    return outcome, gate_calls, translate_inputs


def test_frontmatter_marks_synthesis_fallback(tmp_path):
    """synthesis 降级（source.name=synthesis_fallback）必须落 frontmatter。"""
    _vault(tmp_path)
    _run_pipeline(tmp_path, synth_source="synthesis_fallback")

    fm = _frontmatter(_social(tmp_path).read_text())
    assert "synthesis_source: synthesis_fallback" in fm, "synthesis 降级标记丢失"
    assert "degraded: true" in fm, "降级期必须标 degraded: true"
    assert "synthesis_fallback" in fm


def test_frontmatter_marks_translate_failure(tmp_path):
    """translate 失败（原样返回英文 synth）必须落 frontmatter，不得冒充正常产出。"""
    _vault(tmp_path)

    def failing_translate(synth, **kwargs):
        return synth  # 现网失败签名：原对象返回，未翻译

    _run_pipeline(tmp_path, translate=failing_translate)

    fm = _frontmatter(_social(tmp_path).read_text())
    assert "translate_status: failed" in fm, "翻译失败标记丢失"
    assert "degraded: true" in fm
    assert "translate_failed" in fm


def test_frontmatter_marks_translate_flag_field(tmp_path):
    """translate 通过返回值暴露 translate_failed / translate_degraded 时同样落盘。"""
    _vault(tmp_path)

    def flagged_translate(synth, **kwargs):
        out = json.loads(json.dumps(synth))
        out["translate_degraded"] = True
        return out

    _run_pipeline(tmp_path, translate=flagged_translate)

    fm = _frontmatter(_social(tmp_path).read_text())
    assert "translate_status: degraded" in fm
    assert "degraded: true" in fm


def test_frontmatter_marks_dry_run_intake(tmp_path):
    """--dry-run-intake（fixture 数据）产出必须自报 intake_source=dry_run 且标降级。

    产出位置：staging/dry-run/（缺陷 S-1 起 dry-run 不再写 Social —— 它绕过时间窗
    与跨日去重且不记账，写 Social 会让次日真实运行重复发布同一批内容）。
    """
    _vault(tmp_path)
    _run_pipeline(tmp_path, argv_extra=("--dry-run-intake",))

    assert not _social(tmp_path).exists(), "dry-run 产出不得进 Social"
    dry_out = (
        tmp_path / "Praise Space" / "AKB2.0" / "staging" / "dry-run"
        / f"{DATE} Daily Briefing.md"
    )
    fm = _frontmatter(dry_out.read_text())
    assert "intake_source: dry_run" in fm, "dry-run 产出不得冒充真实抓取"
    assert "degraded: true" in fm
    assert "evidence_paths:\n  - social/account_collector" not in fm, \
        "dry-run 不得把 evidence 写成 account_collector"


def test_frontmatter_records_novelty_mode_normal_run(tmp_path):
    """正常运行也要写清 novelty 判重模式（语义 or sha1 精确），让降级可判读。"""
    _vault(tmp_path)
    _run_pipeline(tmp_path)

    fm = _frontmatter(_social(tmp_path).read_text())
    assert "novelty_mode:" in fm
    assert "intake_source: account_collector" in fm
    assert "synthesis_source: deepseek_synthesis" in fm
    assert "translate_status: ok" in fm
    assert "degraded: false" in fm, "无降级时不应误报"


# ---------------------------------------------------------------------------
# #4 audit 裁决消费
# ---------------------------------------------------------------------------

def test_audit_fail_not_published(tmp_path):
    """verdict=fail → Social 无文件、staging 保留、page_status 非 active、诊断落盘。"""
    _vault(tmp_path)
    fail_verdict = {
        **PASS_VERDICT,
        "verdict": "fail",
        "standard": "pass",
        "llm_audit": "fail",
        "standard_issues": [],
    }
    with pytest.raises(SystemExit) as exc:
        _run_pipeline(tmp_path, verdict=fail_verdict)

    assert exc.value.code == 2
    assert not _social(tmp_path).exists(), "verdict=fail 不得发布"
    fm = _frontmatter(_staging(tmp_path).read_text())
    assert "audit_verdict: fail" in fm
    assert "page_status: active" not in fm


def test_audit_warn_forces_degraded_markers(tmp_path):
    """verdict=warn（llm_audit=skipped）→ 发布但强制标 warn + audit_degraded + 非 active。"""
    _vault(tmp_path)
    warn_verdict = {
        **PASS_VERDICT,
        "verdict": "warn",
        "llm_audit": "skipped",
        "audit_degraded": True,
    }
    outcome, _, _ = _run_pipeline(tmp_path, verdict=warn_verdict)

    assert outcome == 0
    fm = _frontmatter(_social(tmp_path).read_text())
    assert "audit_verdict: warn" in fm
    assert "audit_degraded: true" in fm, "warn 必须带 audit_degraded: true"
    assert "degraded: true" in fm
    assert "page_status: active" not in fm


# ---------------------------------------------------------------------------
# #16 gate 在最终文件上复跑
# ---------------------------------------------------------------------------

def test_gate_reruns_on_final_written_file(tmp_path):
    """gate 被调 2 次；第 2 次入参 == 最终写入的那份文件，内容与 Social 一致。"""
    _vault(tmp_path)
    outcome, gate_calls, _ = _run_pipeline(tmp_path)

    assert outcome == 0
    assert len(gate_calls) == 2, f"gate 应在译前 + 最终文件各跑 1 次，实际 {len(gate_calls)}"
    assert gate_calls[1]["input_file"] == _staging(tmp_path), "第 2 次 gate 必须判最终文件"
    assert gate_calls[1]["text"] == _social(tmp_path).read_text(), \
        "gate 判定的内容必须与写入 Social 的内容逐字相同（三重断言）"
    assert "proposed_gate_decision: active" in gate_calls[1]["text"]


def test_gate_flip_on_final_file_rewrites_frontmatter(tmp_path, capsys):
    """最终文件 gate 判定与首轮不同 → frontmatter 以最终判定重写，stdout 也报最终判定。"""
    _vault(tmp_path)

    def gate_seq(n):
        return "active" if n == 1 else "needs_repair"

    outcome, gate_calls, _ = _run_pipeline(tmp_path, gate=gate_seq)

    assert outcome == 0
    fm = _frontmatter(_social(tmp_path).read_text())
    assert "proposed_gate_decision: needs_repair" in fm, "必须采信最终文件的判定"
    assert "proposed_gate_decision: active" not in fm
    out = capsys.readouterr().out
    assert "proposed_gate_decision: needs_repair" in out, "stdout 必须与 frontmatter 一致"


# ---------------------------------------------------------------------------
# #6 跨日去重账本（进程外）
# ---------------------------------------------------------------------------

def test_cross_day_dedup_drops_previously_published_tweet(tmp_path, monkeypatch):
    """同一 tweet id 连续两天入 pipeline，第二天不出现在产出，且不进翻译。"""
    from akb2.briefing.github.dedup_sqlite import DedupStore
    from akb2.common.state_paths import LEDGER_SUBDIR, state_file_path

    day1_root = _vault(tmp_path / "d1")
    outcome, _, _ = _run_pipeline(day1_root, tweets=[_tweet("AAA"), _tweet("BBB", "@ylecun")])
    assert outcome == 0
    day1_text = _social(day1_root).read_text()
    assert "https://x.com/AAA" in day1_text

    # 账本落在进程外（sqlite 文件），新开 store 也能看到 —— 跨进程可见
    db_path = state_file_path("social-tweet-dedup.sqlite", subdir=LEDGER_SUBDIR)
    assert db_path.exists(), f"跨日去重账本未落盘: {db_path}"
    store = DedupStore(db_path)
    try:
        assert store.is_duplicate("AAA"), "已发布的 tweet id 必须记入账本"
    finally:
        store.close()

    day2_root = _vault(tmp_path / "d2")
    outcome2, _, translate_inputs = _run_pipeline(
        day2_root, tweets=[_tweet("AAA"), _tweet("CCC", "@karpathy")]
    )
    assert outcome2 == 0
    day2_text = _social(day2_root).read_text()
    assert "https://x.com/CCC" in day2_text, "新推文应正常入选"
    assert "https://x.com/AAA" not in day2_text, "昨天已发布的推文不得再次出现（缺陷 #6）"
    sent_ids = [t["id"] for t in translate_inputs[0].get("tweets", [])]
    assert "AAA" not in sent_ids, "去重必须发生在翻译之前（省 token 且防重复译）"


def test_dedup_ledger_unavailable_blocks_publish(tmp_path, monkeypatch):
    """账本落在跨机同步范围（state_paths fail-closed 拒绝）→ 不发布，且原因可见。"""
    _vault(tmp_path)
    # AI-Agent 目录是已知 Syncthing 同步范围，state_paths 会拒绝把账本放在这里
    monkeypatch.setenv("AKB2_STATE_ROOT", str(Path.home() / "AI-Agent" / "not-a-real-state-root"))

    with pytest.raises(SystemExit) as exc:
        _run_pipeline(tmp_path)

    assert exc.value.code == 5
    assert not _social(tmp_path).exists(), "账本不可用时不得发布"


def test_cross_day_dedup_not_marked_when_not_published(tmp_path):
    """audit=fail 未发布 → 账本不记账，次日同批推文仍可入选（不静默烧素材）。"""
    _vault(tmp_path)
    fail_verdict = {**PASS_VERDICT, "verdict": "fail", "llm_audit": "fail"}
    with pytest.raises(SystemExit):
        _run_pipeline(tmp_path, tweets=[_tweet("ZZZ")], verdict=fail_verdict)

    day2_root = _vault(tmp_path / "d2")
    outcome, _, _ = _run_pipeline(day2_root, tweets=[_tweet("ZZZ")])
    assert outcome == 0
    assert "https://x.com/ZZZ" in _social(day2_root).read_text(), \
        "未发布的推文不应被账本吃掉"
