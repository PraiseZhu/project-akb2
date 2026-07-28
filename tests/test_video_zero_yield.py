"""视频链路 2026-07-28 事故回归：0 产出的三个层次。

事故实况：视频链路当天产出 0 个视频，109 个候选全被"[时间闸] 拒收（无法确定发布
日期）"。真实漏斗（当天实测）：

    列表 326 → L1 去重后 222（**全部**要 per-video 补日期，预算只有 40）
             → 时长 215 → **相关性 18** → 窗内 9

三个独立缺陷：

1. **过滤顺序**：时间闸（唯一花 per-video 网络调用的一层）排在时长/关键词/相关性
   三道**纯本地**过滤之前 → 92% 的补取预算烧在必被 L4.5 免费丢掉的候选上，
   排序靠后的源一个日期都拿不到，整批按"日期未知"拒收。
2. **列表阶段无日期**：`--flat-playlist` 对 YouTube 从不返回 upload_date，于是
   补取需求 = 候选总数，40 的预算结构上必然撞顶。`youtubetab:approximate_date`
   让同一次列表调用顺带返回近似日期（真实日期的**上界**），可零成本确定性排除。
3. **静默失败**：`_report_results([])` 返回 0，launchd 只看到正常退出，没有任何
   诊断落盘 —— "采集能力整体失效"与"今天这些博主都没更新"完全无法区分。

每条断言绑定**可观测行为**（补取调用次数 / 拒收计数 / 诊断文件字段 / 退出码），
不是"能跑通"。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import pytest

from src.akb2.video.blocked import (
    STATUS_BLOCKED,
    STATUS_OK,
    STATUS_ZERO_YIELD,
    VIDEO_BLOCKED_REL_PATH,
    classify,
    write_zero_yield_diagnostic,
)
from src.akb2.video.collector import CollectStats, VideoCollector
from src.akb2.video.config import VideoConfig
from src.akb2.video.constants import (
    VIDEO_APPROX_DATE_MARGIN_DAYS,
    VIDEO_MAX_CONSECUTIVE_ZERO_RUNS,
    VIDEO_MAX_DATE_BACKFILL_PER_RUN,
    VIDEO_YT_LIST_EXTRACTOR_ARGS,
)
from tests.video_state_isolation import isolate_video_state  # noqa: F401

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_CREATOR = {
    "id": "yt-test",
    "platform": "youtube",
    "channel_url": "https://www.youtube.com/@TestChannel",
    "name": "Test Channel",
    "enabled": True,
    "max_per_day": 50,
    "keywords": [],
    "tier": "quality",
}


def _config(tmp_path: Path, settings: dict | None = None) -> VideoConfig:
    base = {
        "min_video_duration_minutes": 3,
        "max_video_duration_minutes": 0,
        "days_lookback": 14,
        "dedup_strategy": "none",
        "skip_relevance_filter": False,
        "skip_keyword_filter": True,
        "coldstart": True,
        "coldstart_max_per_day": 50,
    }
    base.update(settings or {})
    sources = tmp_path / "video_sources.json"
    sources.write_text(
        json.dumps({"creators": [_CREATOR], "settings": base}, ensure_ascii=False),
        encoding="utf-8",
    )
    keywords = tmp_path / "keywords.json"
    keywords.write_text(
        json.dumps({
            "include": {"core": ["AI", "LLM", "agent"]},
            "exclude": {"non_ai": ["cooking"]},
        }),
        encoding="utf-8",
    )
    return VideoConfig(
        video_sources_path=sources,
        keywords_path=keywords,
        staging_dir=tmp_path / "staging" / "candidate",
        akb2_root=tmp_path / "vault",
        dedup_db_path=tmp_path / "state" / "video-dedup.sqlite",
    )


def _ago(days: int) -> str:
    return (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")


def _listed(n: int, *, relevant_every: int = 1, prefix: str = "v") -> list[dict]:
    """构造 n 条"列表阶段"候选：无精确日期，标题按 relevant_every 决定是否 AI 相关。

    模拟真实的 YouTube flat-playlist 输出形态（upload_date 恒空）。
    """
    out = []
    for i in range(n):
        ai = (i % relevant_every) == 0
        out.append({
            "platform": "youtube",
            "id": f"{prefix}{i}",
            "title": (f"AI Agents Episode {i}" if ai else f"Cooking Pasta {i}"),
            "url": f"https://www.youtube.com/watch?v={prefix}{i}",
            "duration_minutes": 20,
            "upload_date": "",
            "creator": "Test Channel",
        })
    return out


# ---------------------------------------------------------------------------
# 缺陷 1：过滤顺序 —— 便宜的本地过滤必须先跑，别让 backfill 替它们买单
# ---------------------------------------------------------------------------


def test_backfill_not_spent_on_candidates_local_filters_would_drop(tmp_path):
    """L4.5 会免费丢掉的候选，绝不占用 per-video 日期补取预算。

    回归前：L2 时间闸排在 L4.5 之前 → 100 个候选里 90 个不相关的也各花一次
    `yt-dlp --dump-json`，40 次预算被烧光，剩下的全按"日期未知"拒收（事故当天
    222 个候选抢 40 次预算，109 个直接全灭）。
    """
    collector = VideoCollector(_config(tmp_path))
    # 100 条候选，只有每 10 条里第 1 条与 AI 相关 → 10 条该被补取，90 条不该
    videos = _listed(100, relevant_every=10)

    def _fill(video: dict) -> None:
        video["upload_date"] = _ago(1)

    with mock.patch.object(collector, "_detect_yt_videos", return_value=videos):
        with mock.patch.object(
            collector, "_fill_upload_date", side_effect=_fill
        ) as fill:
            kept = collector._detect_new_videos(_CREATOR)

    assert fill.call_count == 10, (
        f"只应为 L4.5 存活的 10 个候选补日期，实际 {fill.call_count} 次"
        "（>10 说明时间闸又跑在本地过滤之前了）"
    )
    assert fill.call_count <= VIDEO_MAX_DATE_BACKFILL_PER_RUN, "补取需求必须落在预算内"
    assert len(kept) == 10
    assert collector.stats.dropped_relevance == 90
    # 关键：没有任何候选因为"预算被别人烧光"而被拒
    assert collector._date_unknown_rejected == 0
    assert collector.stats.date_backfill_budget_exhausted is False


def test_candidates_exceeding_backfill_budget_still_yield_output(tmp_path):
    """候选数（500）远大于补取预算（40）时，仍有产出 —— 不再整批全灭。

    这是事故的核心可观测行为：`候选数 > 预算` 的结构下，修复后的行为是"相关的少数
    仍能通过"，而不是"109 个候选产出 0 个"。

    这里**不 mock** `_fill_upload_date`，只 mock 它底下的 `subprocess.run` ——
    真实的预算判定（`_date_backfill_used >= VIDEO_MAX_DATE_BACKFILL_PER_RUN`）必须
    真的参与，否则测的是 mock 的行为而不是预算的行为。
    """
    collector = VideoCollector(_config(tmp_path))
    # 500 条里只有 5 条相关（索引 0/100/200/300/400）→ 相关的那几条分散在很后面，
    # 一旦预算被前面 400 多个不相关候选吃掉，它们必然全被"日期未知"拒收。
    videos = _listed(500, relevant_every=100)
    payload = json.dumps({"upload_date": _ago(2)})
    completed = mock.Mock(stdout=payload, returncode=0, stderr="")

    with mock.patch.object(collector, "_detect_yt_videos", return_value=videos):
        with mock.patch("src.akb2.video.collector.subprocess.run",
                        return_value=completed) as run:
            with mock.patch("src.akb2.video.collector.time.sleep"):
                kept = collector._detect_new_videos(_CREATOR)

    assert len(videos) > VIDEO_MAX_DATE_BACKFILL_PER_RUN, "前提：候选数必须超过预算"
    assert len(kept) == 5, (
        f"5 个相关候选应全部通过，实际 {len(kept)} —— 少于 5 说明预算又被"
        "本地过滤本该丢掉的候选吃光了"
    )
    assert run.call_count == 5, f"per-video 取数只该发生 5 次，实际 {run.call_count}"
    assert collector.stats.date_backfill_budget_exhausted is False
    assert collector.stats.rejected_date_unknown == 0


def test_budget_exhaustion_is_recorded_as_observable_fact(tmp_path):
    """补取预算真撞顶时必须留下可观测标记（不只是一行 warning）。

    修复不是"把 40 调大"，所以"预算仍可能撞顶"这条路径必须保持有界且可见。
    """
    collector = VideoCollector(_config(tmp_path, {"skip_relevance_filter": True}))
    videos = _listed(VIDEO_MAX_DATE_BACKFILL_PER_RUN + 5, relevant_every=1)

    def _fill_real(video: dict) -> None:
        # 复用真实的预算判定逻辑，只把网络调用换成"总能取到日期"
        if collector._date_backfill_used >= VIDEO_MAX_DATE_BACKFILL_PER_RUN:
            collector.stats.date_backfill_budget_exhausted = True
            return
        collector._date_backfill_used += 1
        collector.stats.date_backfill_used = collector._date_backfill_used
        video["upload_date"] = _ago(1)

    with mock.patch.object(collector, "_detect_yt_videos", return_value=videos):
        with mock.patch.object(collector, "_fill_upload_date", side_effect=_fill_real):
            kept = collector._detect_new_videos(_CREATOR)

    assert collector.stats.date_backfill_budget_exhausted is True
    assert len(kept) == VIDEO_MAX_DATE_BACKFILL_PER_RUN
    assert collector.stats.rejected_date_unknown == 5, "撞顶后的候选仍 fail-closed 拒收"


# ---------------------------------------------------------------------------
# 缺陷 2：列表阶段近似日期 —— 免费的确定性排除，但绝不放宽准入
# ---------------------------------------------------------------------------


def test_yt_list_call_requests_approximate_date(tmp_path):
    """YouTube 列表调用必须带 `youtubetab:approximate_date`。

    不带 = 列表永远无日期 = 每个候选都要 per-video 补取 = 回到 40 撞顶的老路。
    """
    collector = VideoCollector(_config(tmp_path))
    completed = mock.Mock(stdout="", returncode=0, stderr="")
    with mock.patch("subprocess.run", return_value=completed) as run:
        collector._detect_yt_videos(_CREATOR, days=14)

    argv = run.call_args[0][0]
    assert "--extractor-args" in argv
    assert VIDEO_YT_LIST_EXTRACTOR_ARGS in argv
    assert argv[argv.index("--extractor-args") + 1] == VIDEO_YT_LIST_EXTRACTOR_ARGS


def test_list_stage_date_lands_in_approx_field_not_upload_date(tmp_path):
    """列表阶段的日期只能落 `upload_date_approx`，`upload_date` 必须留空。

    近似值一旦写进 `upload_date`，`if not v.get("upload_date")` 就不再触发补取，
    近似日期会被当成精确日期直接通过时间闸 —— 那正是"为了有产出放宽时间闸"
    （违反宪法 P3）。
    """
    collector = VideoCollector(_config(tmp_path))
    line = json.dumps({
        "id": "abc", "title": "AI Agents", "url": "https://www.youtube.com/watch?v=abc",
        "duration": 1200, "upload_date": _ago(3),
    })
    completed = mock.Mock(stdout=line + "\n", returncode=0, stderr="")
    with mock.patch("subprocess.run", return_value=completed):
        videos = collector._detect_yt_videos(_CREATOR, days=14)

    assert len(videos) == 1
    assert videos[0]["upload_date"] == "", "近似日期不得冒充精确日期"
    assert videos[0]["upload_date_approx"] == _ago(3)


def test_approx_date_prefilter_skips_backfill_for_clearly_old_videos(tmp_path):
    """近似日期已远早于窗口 → 零网络成本直接排除，不花补取预算。"""
    collector = VideoCollector(_config(tmp_path))
    old = _listed(1)[0]
    old["upload_date_approx"] = _ago(200)

    with mock.patch.object(collector, "_detect_yt_videos", return_value=[old]):
        with mock.patch.object(collector, "_fill_upload_date") as fill:
            kept = collector._detect_new_videos(_CREATOR)

    assert kept == []
    assert fill.call_count == 0, "确定超窗的候选不该再花 per-video 调用"
    assert collector.stats.rejected_date_prefilter == 1
    assert collector.stats.rejected_date_unknown == 0, "这不是'日期未知'，是'已知很旧'"


def test_approx_date_inside_window_still_requires_exact_date(tmp_path):
    """近似日期在窗内**不足以**放行：仍必须补到精确日期才通过（fail-closed 不放宽）。

    近似值是真实日期的上界 → "近似在窗内"推不出"真实在窗内"，所以准入只认精确值。
    """
    collector = VideoCollector(_config(tmp_path))
    fresh = _listed(1)[0]
    fresh["upload_date_approx"] = _ago(1)

    with mock.patch.object(collector, "_detect_yt_videos", return_value=[fresh]):
        # 补取拿不到精确日期（限频 / cookies 失效）
        with mock.patch.object(collector, "_fill_upload_date") as fill:
            kept = collector._detect_new_videos(_CREATOR)

    assert fill.call_count == 1, "近似在窗内必须触发精确补取"
    assert kept == [], "精确日期缺失 → 拒收（不得用近似值顶替）"
    assert collector.stats.rejected_date_unknown == 1


def test_approx_prefilter_honours_safety_margin(tmp_path):
    """近似日期只比窗口下界早一点（在安全边距内）→ 不预筛，走精确补取。

    近似值有小时级偏移风险，边距内的候选必须交给精确日期裁决，绝不误杀。
    """
    collector = VideoCollector(_config(tmp_path))
    borderline = _listed(1)[0]
    # 窗口 14 天，边距 2 天 → 15 天前落在 [下界-边距, 下界) 内，不该被预筛掉
    borderline["upload_date_approx"] = _ago(14 + VIDEO_APPROX_DATE_MARGIN_DAYS - 1)

    with mock.patch.object(collector, "_detect_yt_videos", return_value=[borderline]):
        with mock.patch.object(collector, "_fill_upload_date") as fill:
            collector._detect_new_videos(_CREATOR)

    assert collector.stats.rejected_date_prefilter == 0, "边距内不得预筛"
    assert fill.call_count == 1, "边距内应交给精确补取裁决"


def test_exact_date_out_of_window_still_rejected(tmp_path):
    """补到的精确日期超窗 → 照旧拒收（近似日期预筛不是放行通道）。"""
    collector = VideoCollector(_config(tmp_path))
    v = _listed(1)[0]
    v["upload_date_approx"] = _ago(3)  # 近似看起来很新

    def _fill(video: dict) -> None:
        video["upload_date"] = _ago(400)  # 精确日期其实很旧

    with mock.patch.object(collector, "_detect_yt_videos", return_value=[v]):
        with mock.patch.object(collector, "_fill_upload_date", side_effect=_fill):
            kept = collector._detect_new_videos(_CREATOR)

    assert kept == []
    assert collector.stats.dropped_out_of_window == 1


def test_missing_approx_date_batch_is_not_silent(tmp_path, caplog):
    """列表整批拿不到近似日期（extractor-args 失效）必须告警 —— 这是退化前兆。"""
    collector = VideoCollector(_config(tmp_path))
    line = json.dumps({
        "id": "abc", "title": "AI Agents", "url": "https://www.youtube.com/watch?v=abc",
        "duration": 1200,
    })
    completed = mock.Mock(stdout=line + "\n", returncode=0, stderr="")
    with caplog.at_level("WARNING"):
        with mock.patch("subprocess.run", return_value=completed):
            collector._detect_yt_videos(_CREATOR, days=14)

    assert "全无近似日期" in caplog.text, "近似日期整批缺失必须留 WARNING"


# ---------------------------------------------------------------------------
# 缺陷 3：0 产出可观测 —— 归因 + 诊断落盘 + 退出码
# ---------------------------------------------------------------------------


def test_zero_yield_with_exhausted_budget_is_blocked_not_ok():
    """预算撞顶导致的 0 产出 = blocked（不是"今天没有新视频"）。"""
    stats = CollectStats(
        sources_enabled=21, raw_listed=326, dropped_dedup=104,
        rejected_date_unknown=109, date_backfill_used=40, date_backfill_ok=40,
        date_backfill_budget_exhausted=True,
    ).as_dict()
    verdict = classify(stats, published=0)

    assert verdict.status == STATUS_BLOCKED
    assert verdict.reason == "date_backfill_budget_exhausted"
    assert "40" in verdict.detail


def test_zero_yield_with_all_backfill_failed_is_blocked():
    """补取全失败（限频 / cookies）导致的 0 产出 = blocked。"""
    stats = CollectStats(
        sources_enabled=21, raw_listed=326, rejected_date_unknown=18,
        date_backfill_used=18, date_backfill_ok=0, date_backfill_failed=18,
    ).as_dict()
    verdict = classify(stats, published=0)

    assert verdict.status == STATUS_BLOCKED
    assert verdict.reason == "date_backfill_all_failed"


def test_zero_yield_with_empty_list_stage_is_blocked():
    """列表阶段全空（yt-dlp 坏 / cookies 失效）= blocked。"""
    stats = CollectStats(sources_enabled=21, raw_listed=0).as_dict()
    verdict = classify(stats, published=0)

    assert verdict.status == STATUS_BLOCKED
    assert verdict.reason == "list_stage_empty"


def test_zero_yield_with_broken_titles_is_blocked():
    """上游元数据整体坏（补全后仍无 title）= blocked，且真因不是"列表返回空"。"""
    stats = CollectStats(sources_enabled=21, raw_listed=0, dropped_no_title=20).as_dict()
    verdict = classify(stats, published=0)

    assert verdict.status == STATUS_BLOCKED
    assert verdict.reason == "upstream_metadata_broken"


def test_genuine_zero_yield_is_not_blocked():
    """各层计数正常、候选确实都超窗 → zero_yield（信息性，不是故障）。"""
    stats = CollectStats(
        sources_enabled=21, raw_listed=326, dropped_dedup=104,
        dropped_relevance=200, rejected_date_prefilter=20, dropped_out_of_window=2,
        date_backfill_used=2, date_backfill_ok=2,
    ).as_dict()
    verdict = classify(stats, published=0)

    assert verdict.status == STATUS_ZERO_YIELD
    assert verdict.reason == "no_candidates_in_window"
    assert verdict.is_blocked is False


def test_consecutive_zero_yield_escalates_to_blocked():
    """连续 N 轮"真零"要升级成 blocked —— 有界升级，不无限沉默。"""
    stats = CollectStats(sources_enabled=21, raw_listed=326, dropped_relevance=326).as_dict()

    below = classify(stats, published=0,
                     previous_zero_runs=VIDEO_MAX_CONSECUTIVE_ZERO_RUNS - 2)
    assert below.status == STATUS_ZERO_YIELD

    at_threshold = classify(stats, published=0,
                            previous_zero_runs=VIDEO_MAX_CONSECUTIVE_ZERO_RUNS - 1)
    assert at_threshold.status == STATUS_BLOCKED
    assert at_threshold.reason == "consecutive_zero_yield"


def test_diagnostic_file_written_with_reason_on_blocked(tmp_path):
    """blocked 时诊断文件必须落盘且带机读真因 + 漏斗计数。"""
    stats = CollectStats(
        sources_enabled=21, raw_listed=326, rejected_date_unknown=109,
        date_backfill_used=40, date_backfill_ok=40,
        date_backfill_budget_exhausted=True,
    ).as_dict()
    verdict = write_zero_yield_diagnostic(stats, published=0, repo_root=tmp_path)

    path = tmp_path / VIDEO_BLOCKED_REL_PATH
    assert path.exists(), "0 产出必须留下机读诊断（宪法 P4）"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["status"] == STATUS_BLOCKED
    assert data["reason"] == "date_backfill_budget_exhausted"
    assert data["published_count"] == 0
    assert data["consecutive_zero_runs"] == 1
    assert data["funnel"]["rejected_date_unknown"] == 109
    assert verdict.is_blocked is True


def test_consecutive_counter_accumulates_across_runs(tmp_path):
    """连续 0 产出计数跨轮累加（复用诊断文件自身，不引入第二个状态文件）。"""
    stats = CollectStats(
        sources_enabled=21, raw_listed=326, dropped_relevance=326
    ).as_dict()
    first = write_zero_yield_diagnostic(stats, published=0, repo_root=tmp_path)
    second = write_zero_yield_diagnostic(stats, published=0, repo_root=tmp_path)

    assert first.consecutive_zero_runs == 1
    assert second.consecutive_zero_runs == 2


def test_successful_run_clears_reason(tmp_path):
    """有产出后诊断必须清掉 reason —— 否则恢复之后告警还在引用旧故障。"""
    blocked_stats = CollectStats(
        sources_enabled=21, raw_listed=326, rejected_date_unknown=109,
        date_backfill_budget_exhausted=True, date_backfill_used=40,
    ).as_dict()
    write_zero_yield_diagnostic(blocked_stats, published=0, repo_root=tmp_path)

    ok_stats = CollectStats(sources_enabled=21, raw_listed=326,
                            candidates_after_filters=3).as_dict()
    verdict = write_zero_yield_diagnostic(ok_stats, published=3, repo_root=tmp_path)

    data = json.loads((tmp_path / VIDEO_BLOCKED_REL_PATH).read_text(encoding="utf-8"))
    assert verdict.status == STATUS_OK
    assert "reason" not in data
    assert data["consecutive_zero_runs"] == 0
    assert data["published_count"] == 3


def test_feishu_reader_can_surface_video_reason(tmp_path):
    """已有的读取侧（feishu_sync.blocked）能读出视频链路真因。"""
    from src.akb2.feishu_sync.blocked import blocking_reason

    stats = CollectStats(
        sources_enabled=21, raw_listed=326, rejected_date_unknown=109,
        date_backfill_used=40, date_backfill_budget_exhausted=True,
    ).as_dict()
    write_zero_yield_diagnostic(stats, published=0, repo_root=tmp_path)
    today = datetime.now().strftime("%Y-%m-%d")

    text = blocking_reason("video", today, repo_root=tmp_path)
    assert text is not None
    assert "date_backfill_budget_exhausted" in text


def test_diagnostic_write_failure_does_not_raise(tmp_path):
    """诊断落盘失败只记 ERROR，绝不把主链路搞挂。"""
    stats = CollectStats(sources_enabled=21, raw_listed=326).as_dict()
    with mock.patch.object(Path, "write_text", side_effect=OSError("read-only fs")):
        verdict = write_zero_yield_diagnostic(stats, published=0, repo_root=tmp_path)
    assert verdict is not None


# ---------------------------------------------------------------------------
# 退出码：blocked 必须让 launchd 看见
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("published", "stats_kwargs", "expect_rc"),
    [
        # 0 产出 + 预算撞顶 → blocked → 退出码非 0（原来恒为 0，静默失败）
        (0, {"sources_enabled": 21, "raw_listed": 326, "rejected_date_unknown": 109,
             "date_backfill_used": 40, "date_backfill_budget_exhausted": True}, 1),
        # 0 产出但归因为窗口内确实没有新视频 → 正常退出
        (0, {"sources_enabled": 21, "raw_listed": 326, "dropped_relevance": 326}, 0),
    ],
)
def test_run_full_exit_code_reflects_blocked_state(
    tmp_path, monkeypatch, published, stats_kwargs, expect_rc
):
    """`_run_full` 的退出码必须区分"被阻断的 0 产出"与"真的没有新视频"。"""
    from src.akb2.video import __main__ as video_main
    from src.akb2.video import blocked as blocked_mod

    # 诊断落盘的仓库根改指临时目录，别写真实 logs/
    monkeypatch.setattr(blocked_mod, "REPO_ROOT", tmp_path, raising=True)

    collector = mock.Mock()
    collector.stats = CollectStats(**stats_kwargs)
    results = [{"status": "ok"} for _ in range(published)]

    monkeypatch.setattr(video_main, "_build_llm_adapter", lambda cfg: None)
    monkeypatch.setattr(
        video_main, "_build_dedup_pair", lambda cfg, llm=None: (mock.Mock(), collector)
    )
    monkeypatch.setattr(video_main, "_seed_history", lambda store, cfg: None)
    monkeypatch.setattr(video_main, "VideoResearcher", lambda *a, **k: mock.Mock())
    monkeypatch.setattr(video_main, "Extractor", lambda *a, **k: mock.Mock())
    monkeypatch.setattr(video_main, "Reasoner", lambda *a, **k: mock.Mock())
    monkeypatch.setattr(video_main, "ConceptStore", lambda *a, **k: mock.Mock())

    fake_assembler = mock.Mock()
    fake_assembler.run_pipeline.return_value = results
    monkeypatch.setattr(video_main, "Assembler", lambda *a, **k: fake_assembler)

    rc = video_main._run_full(_config(tmp_path), dry_run=False)
    assert rc == expect_rc
    assert (tmp_path / VIDEO_BLOCKED_REL_PATH).exists()
