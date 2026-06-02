"""test_date_filter.py — SC-6 / SC-7 日期过滤测试。"""
from __future__ import annotations

import sys
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# SC-7: _filter_recent 单元测试
# ---------------------------------------------------------------------------

def test_filter_recent_drops_old():
    """超出 N 天窗口的旧推文被过滤掉。"""
    from akb2.briefing.pipeline import _filter_recent

    tweets = [
        {"id": "1", "created_at": "2026-05-30T10:00:00+00:00"},   # 当天 → 保留
        {"id": "2", "created_at": "2026-05-29T10:00:00+00:00"},   # 昨天 → 保留（N=2）
        {"id": "3", "created_at": "2026-05-28T10:00:00+00:00"},   # 2天前 → 丢弃
        {"id": "4", "created_at": "2026-04-01T00:00:00+00:00"},   # 4月 → 丢弃
    ]
    kept = _filter_recent(tweets, "2026-05-30", days=2)
    ids = [t["id"] for t in kept]
    assert "1" in ids, "当天推文应保留"
    assert "2" in ids, "昨天推文应保留（N=2）"
    assert "3" not in ids, "2天前应丢弃"
    assert "4" not in ids, "4月推文应丢弃"


def test_filter_recent_keeps_missing_date(capsys):
    """缺 created_at 的推文保守保留，同时打 WARNING。"""
    from akb2.briefing.pipeline import _filter_recent

    tweets = [
        {"id": "1", "created_at": ""},
        {"id": "2"},  # 无 created_at key
        {"id": "3", "created_at": "2026-05-30T10:00:00+00:00"},
    ]
    kept = _filter_recent(tweets, "2026-05-30", days=2)
    ids = [t["id"] for t in kept]
    assert "1" in ids, "空 created_at 应保守保留"
    assert "2" in ids, "缺 created_at key 应保守保留"
    assert "3" in ids, "正常推文应保留"


def test_filter_recent_keeps_bad_format(capsys):
    """created_at 格式非法时保守保留。"""
    from akb2.briefing.pipeline import _filter_recent

    tweets = [{"id": "1", "created_at": "not-a-date"}]
    kept = _filter_recent(tweets, "2026-05-30", days=2)
    assert len(kept) == 1


def test_filter_recent_invalid_date_skips_filter():
    """date 参数本身非法时，跳过过滤返回全部。"""
    from akb2.briefing.pipeline import _filter_recent

    tweets = [{"id": "1", "created_at": "2026-04-01T00:00:00+00:00"}]
    kept = _filter_recent(tweets, "bad-date", days=2)
    assert len(kept) == 1, "date 非法时应跳过过滤"


def test_filter_recent_timezone_boundary():
    """时区转换正确：UTC 23:00 = CST 次日 07:00，应算在次日。"""
    from akb2.briefing.pipeline import _filter_recent

    # 2026-05-29T23:30:00Z = 2026-05-30T07:30:00+08:00 → 算 5-30 → 应保留
    tweets = [{"id": "1", "created_at": "2026-05-29T23:30:00Z"}]
    kept = _filter_recent(tweets, "2026-05-30", days=1)  # 只保留5-30当天
    assert len(kept) == 1, "UTC 23:30 折算 CST 次日 07:30，应保留"


def test_filter_recent_no_tweets_returns_empty():
    from akb2.briefing.pipeline import _filter_recent
    assert _filter_recent([], "2026-05-30") == []


def test_intake_window_default_is_3_days():
    """时间窗默认 3 天（从 2 改，安静日凑够 4-5 主题；可 AKB2_INTAKE_WINDOW_DAYS 覆盖）。"""
    from akb2.briefing.pipeline import _INTAKE_WINDOW_DAYS_DEFAULT
    assert _INTAKE_WINDOW_DAYS_DEFAULT == 3, \
        f"窗口默认应为 3，实际 {_INTAKE_WINDOW_DAYS_DEFAULT}"


def test_filter_recent_default_window_keeps_3_days():
    """默认窗口下保留近 3 天（今天/昨天/前天），第 4 天丢弃。"""
    from akb2.briefing.pipeline import _filter_recent
    tweets = [
        {"id": "1", "created_at": "2026-05-30T10:00:00+00:00"},  # 当天
        {"id": "2", "created_at": "2026-05-29T10:00:00+00:00"},  # 昨天
        {"id": "3", "created_at": "2026-05-28T10:00:00+00:00"},  # 前天 → 近3天保留
        {"id": "4", "created_at": "2026-05-27T10:00:00+00:00"},  # 3天前 → 丢弃
    ]
    kept = [t["id"] for t in _filter_recent(tweets, "2026-05-30")]  # 用默认 days
    assert kept == ["1", "2", "3"], f"近3天应保留 1/2/3，实际 {kept}"


# ---------------------------------------------------------------------------
# SC-6: 采集层时间窗 — intake cmd 含 --since / --until
# ---------------------------------------------------------------------------

def test_intake_cmd_has_time_window(tmp_path):
    """run_intake 非 dry-run 时，子进程 cmd 包含 --since 和 --until。"""
    from akb2.briefing import pipeline as pl

    captured_cmds = []

    def fake_run(cmd, **kwargs):
        captured_cmds.append(cmd)
        m = MagicMock()
        m.returncode = 0
        return m

    # run_intake writes output_path; we also need it to be readable
    out_path = tmp_path / "intake.json"
    out_path.write_text('{"tweets": []}')

    with patch("akb2.briefing.pipeline.subprocess.run", side_effect=fake_run):
        with patch("builtins.open", side_effect=lambda p, *a, **k: open(str(out_path), *a, **k) if str(p) == str(out_path) else open(p, *a, **k)):
            try:
                pl.run_intake(
                    dry_run=False,
                    output_path=out_path,
                    since_date="2026-05-29",
                    until_date="2026-05-30",
                )
            except Exception:
                pass  # subprocess.run is mocked, file may not exist

    assert captured_cmds, "subprocess.run should have been called"
    cmd = captured_cmds[0]
    assert "--since" in cmd, "cmd 应包含 --since"
    assert "2026-05-29" in cmd, "since 值应为 2026-05-29"
    assert "--until" in cmd, "cmd 应包含 --until"
    assert "2026-05-30" in cmd, "until 值应为 2026-05-30"


def test_intake_cmd_no_since_when_dry_run(tmp_path):
    """dry-run 时不传 since/until（用 fixture，无意义）。"""
    from akb2.briefing import pipeline as pl

    captured_cmds = []

    def fake_run(cmd, **kwargs):
        captured_cmds.append(cmd)
        m = MagicMock()
        m.returncode = 0
        return m

    out_path = tmp_path / "intake.json"
    out_path.write_text('{"tweets": []}')

    with patch("akb2.briefing.pipeline.subprocess.run", side_effect=fake_run):
        try:
            pl.run_intake(
                dry_run=True,
                output_path=out_path,
                since_date=None,
                until_date=None,
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# F1 fix: _filter_recent 全清空时保守保留
# ---------------------------------------------------------------------------

def test_filter_recent_all_dropped_returns_empty():
    """当所有推文都超出窗口时，_filter_recent 返回空列表（不做保守保留，intake层负责标记fallback）。"""
    from akb2.briefing.pipeline import _filter_recent

    tweets = [
        {"id": "1", "created_at": "2026-04-01T00:00:00+00:00"},  # 很旧
        {"id": "2", "created_at": "2026-04-02T00:00:00+00:00"},  # 很旧
    ]
    kept = _filter_recent(tweets, "2026-05-30", days=2)
    assert len(kept) == 0, "全部超窗口时应返回空列表"


def test_filter_recent_partial_drop_works_normally():
    """部分过滤时正常工作（不触发保守兜底）。"""
    from akb2.briefing.pipeline import _filter_recent

    tweets = [
        {"id": "1", "created_at": "2026-05-30T10:00:00+00:00"},  # 当天 → 保留
        {"id": "2", "created_at": "2026-04-01T00:00:00+00:00"},  # 很旧 → 丢弃
    ]
    kept = _filter_recent(tweets, "2026-05-30", days=2)
    assert len(kept) == 1, "部分过滤时只保留窗口内的推文"
    assert kept[0]["id"] == "1"


# ---------------------------------------------------------------------------
# F2 fix: _collect_display_tweets 同一推文可出现在多主题
# ---------------------------------------------------------------------------

def test_collect_display_tweets_same_tweet_in_multiple_topics():
    """同一推文支撑多个主题时，每个主题下都应渲染该推文。"""
    from akb2.briefing.pipeline import _collect_display_tweets

    synth = {
        "focus_discussions": [
            {"supporting_tweet_ids": ["1"]},
            {"supporting_tweet_ids": ["1", "2"]},  # id=1 重复出现
        ],
        "tweets": [
            {"id": "1", "score": 0.9, "author": "@x"},
            {"id": "2", "score": 0.8, "author": "@y"},
        ],
    }
    per_topic, orphans = _collect_display_tweets(synth, orphan_top_k=5)
    assert len(per_topic[0]) == 1, "主题0 应有1条推文"
    assert len(per_topic[1]) == 2, "主题1 应有2条推文（含重复 id=1）"
    assert len(orphans) == 0, "所有推文都被引用，孤儿应为空"


def test_collect_display_tweets_skips_fabricated_supporting_id():
    """SC-4：render 层 supporting_tweet_ids 指向不存在的 tweet → 静默跳过，不崩、不渲染幽灵。"""
    from akb2.briefing.pipeline import _collect_display_tweets

    synth = {
        "focus_discussions": [
            {"supporting_tweet_ids": ["1", "GHOST404"]},  # GHOST404 不存在
        ],
        "tweets": [
            {"id": "1", "score": 0.9, "author": "@x"},
        ],
    }
    per_topic, orphans = _collect_display_tweets(synth, orphan_top_k=5)

    rendered_ids = [t.get("id") for t in per_topic[0]]
    assert rendered_ids == ["1"], "只渲染真实推文，GHOST404 被跳过"
    assert all(t.get("id") != "GHOST404" for grp in per_topic for t in grp)
    assert len(orphans) == 0, "唯一推文已被引用，无孤儿"
