"""生产调用路径回归 — 断言「接线本身」，不是「被正确调用时行为正确」。

本项目的经典失败模式：新增 API + 新增测试 + 测试全绿，但生产调用方从未接上
（`test_L1_url_dedup` 在测试里手工传 `db=db`，生产 `__main__.py` 从不传 → 79%
重复在 CI 全绿下跑了一个月）。本文件的每条断言都必须挂在真实 pipeline 入口
`run_pipeline()` 上，spy 生产实参 / 读生产产物，不允许「测试自己把参数传对」。

覆盖：
- GH-1 `mark_seen(url, first_mention_at)` — 写端实参必须带第二判据
- GH-2 Markdown 标题必须保留 GitHub 侧真实大小写（去重键仍全小写）
- GH-3 `DedupStore` 窗口下限必须由调用方链路声明，且抬升可见
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from akb2.briefing.github.dedup_sqlite import DEDUP_WINDOW_DAYS, DedupStore
from akb2.briefing.github.intake import Candidate
from akb2.briefing.github.intake.twitter_collector import RECENCY_WINDOW_DAYS
from akb2.briefing.github.pipeline import run_pipeline

_SUMMARY = "这是一个用于测试的 AI 仓库，覆盖机器学习工作流与流水线校验。"
_USE_CASE = "正向场景：搭建 AI 流水线时使用。反向场景：生产环境不要直接依赖。"


def _enrich_ok(url: str, full_name: str | None = None) -> dict:
    h = sum(ord(c) for c in url)
    return {
        "repo_url": url,
        "requested_repo_url": url,
        "repo_full_name": full_name,
        "stars": 500 + h % 500,
        "language": "Python",
        "topic_tags": ["ai"],
        "created_at": "2024-01-01T00:00:00Z",
        "readme_excerpt": "x" * 100,
        "commits_7d": 5,
        "enrich_ok": True,
    }


def _mock_synth(repos, api_key=None, *, mock=False):
    return {"top_repos": [
        {**r, "summary": _SUMMARY, "use_case": _USE_CASE} for r in repos
    ]}


def _candidates(urls: list[str], mentioned_at: str) -> list[Candidate]:
    return [Candidate(u, "@github_trending", None, mentioned_at, None) for u in urls]


@pytest.fixture
def vault(tmp_path):
    v = tmp_path / "vault"
    (v / "Praise Space/AKB2.0/AI TRENDS/GitHub").mkdir(parents=True)
    return v


def _run(cands, novelty_db, vault_path, monkeypatch, *, full_names=None):
    """跑真实 pipeline 入口（layer2 直入，避开 SC-1 L2 补位分支）。"""
    monkeypatch.setenv("AKB2_NOVELTY_DB", str(novelty_db))
    monkeypatch.setenv("VAULT_ROOT", str(vault_path))
    monkeypatch.setenv("AKB2_STAR_HISTORY_DB", str(novelty_db.parent / "star.sqlite"))
    names = full_names or {}

    with (
        patch("akb2.briefing.github.pipeline._real_intake",
              return_value=(cands, "layer2", {})),
        patch("akb2.briefing.github.pipeline.enrich",
              side_effect=lambda u: _enrich_ok(u, names.get(u))),
        patch("akb2.briefing.github.pipeline.synthesize", side_effect=_mock_synth),
    ):
        return run_pipeline(mock=False)


def _backdate_all(db_path, days: float) -> None:
    """把账本里所有 seen_at 真实回拨 days 天（驱动真实时间间隔，不是 mock 形状）。"""
    store = DedupStore(db_path)
    seen_at = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    store._conn.execute("UPDATE seen_urls SET seen_at = ?", (seen_at,))
    store._conn.commit()
    store.close()


class TestGH1MarkSeenWiring:
    """GH-1：写端必须把 first_mention_at 一起落库，否则不变式 2 在生产恒 False。"""

    def test_pipeline_passes_first_mention_at_to_mark_seen(
        self, tmp_path, vault, monkeypatch
    ):
        """spy 住 DedupStore.mark_seen，跑真实 pipeline，断言实参带 first_mention_at。

        缺陷形态：pipeline 调 `mark_seen(r["repo_url"])`（少第二个实参）→ 落库
        `(url, seen_at, NULL)`。此断言不看行为，只看生产实参。
        """
        now = datetime.now(UTC).isoformat()
        urls = [f"https://github.com/trending/repo{i}" for i in range(7)]
        novelty_db = tmp_path / "novelty.sqlite"

        real_mark_seen = DedupStore.mark_seen
        calls: list[tuple[str, str | None]] = []

        def spy(self, normalized_url, first_mention_at=None):
            calls.append((normalized_url, first_mention_at))
            return real_mark_seen(self, normalized_url, first_mention_at)

        with patch.object(DedupStore, "mark_seen", spy):
            rc = _run(_candidates(urls, now), novelty_db, vault, monkeypatch)

        assert rc == 0, "前置条件：本用例需要 pipeline 真正写盘到 mark_seen 那一步"
        assert calls, "pipeline 未调用 mark_seen — 前置条件不成立"
        missing = [u for u, fm in calls if not fm]
        assert not missing, (
            "mark_seen 生产实参缺 first_mention_at（读端第二判据将恒为 NULL）："
            f"{missing}"
        )

    def test_first_mention_at_actually_persisted_by_pipeline(
        self, tmp_path, vault, monkeypatch
    ):
        """直接查生产账本：first_mention_at 列不得为 NULL。"""
        now = datetime.now(UTC).isoformat()
        urls = [f"https://github.com/trending/repo{i}" for i in range(7)]
        novelty_db = tmp_path / "novelty.sqlite"

        assert _run(_candidates(urls, now), novelty_db, vault, monkeypatch) == 0

        store = DedupStore(novelty_db)
        rows = store._conn.execute(
            "SELECT normalized_url, first_mention_at FROM seen_urls"
        ).fetchall()
        store.close()
        assert rows, "账本为空 — 前置条件不成立"
        assert all(fm for _, fm in rows), f"落库行判据列为 NULL: {rows}"

    def test_second_run_without_new_mention_is_blocked_end_to_end(
        self, tmp_path, vault, monkeypatch
    ):
        """两轮 pipeline：第 2 轮同一批 repo、同一 first_mention_at、间隔 > 去重窗口
        → 必须全部被拦（无新提及不复推），产不出简报。

        这是 GH-1 真正要消掉的 128/179 = 71.5%：同一条旧推文被反复当新事件触发。
        写端不传 first_mention_at 时，第 2 轮会照常出报（本断言随即失败）。
        """
        mentioned_at = "2026-06-01T10:00:00+00:00"
        urls = [f"https://github.com/trending/repo{i}" for i in range(7)]
        novelty_db = tmp_path / "novelty.sqlite"
        cands = _candidates(urls, mentioned_at)

        assert _run(cands, novelty_db, vault, monkeypatch) == 0, "第 1 轮应正常出报"

        # 真实回拨到去重窗口之外 → 不变式 1 已不适用，只剩不变式 2 能拦
        _backdate_all(novelty_db, DEDUP_WINDOW_DAYS + 3)
        monkeypatch.setattr(
            "akb2.briefing.github.pipeline._PROJECT_ROOT", tmp_path, raising=False
        )

        rc2 = _run(cands, novelty_db, vault, monkeypatch)
        assert rc2 != 0, "第 2 轮无新提及却出了报 — 「无新提及不复推」在生产空转"

        import json
        skipped = json.loads((tmp_path / "briefing-skipped.json").read_text())
        assert skipped["reason"] == "too_few_aggregated_repos", skipped
        assert skipped["diag"]["novelty_dropped"] == len(urls), skipped

    def test_second_run_with_new_mention_still_repushes(
        self, tmp_path, vault, monkeypatch
    ):
        """反向保护：真有更新的提及时间 → 允许复推，不被误杀。"""
        urls = [f"https://github.com/trending/repo{i}" for i in range(7)]
        novelty_db = tmp_path / "novelty.sqlite"

        assert _run(
            _candidates(urls, "2026-06-01T10:00:00+00:00"),
            novelty_db, vault, monkeypatch,
        ) == 0
        _backdate_all(novelty_db, DEDUP_WINDOW_DAYS + 3)

        rc2 = _run(
            _candidates(urls, "2026-06-20T09:00:00+00:00"),
            novelty_db, vault, monkeypatch,
        )
        assert rc2 == 0, "有新提及仍被拦 = 过度去重，把真新事件也杀了"


class TestGH2DisplayNameWiring:
    """GH-2：Markdown 标题必须保留真实大小写；去重键必须仍是全小写。"""

    def test_rendered_markdown_title_preserves_case(self, tmp_path, vault, monkeypatch):
        """跑真实 pipeline，读写出的 .md，断言标题是 `id-Software/Quake`。

        缺陷形态：渲染器从小写去重键切名 → 标题渲染成 `id-software/quake`。
        """
        now = datetime.now(UTC).isoformat()
        cased = {
            "https://github.com/id-software/quake": "id-Software/Quake",
            "https://github.com/xianfei/sysmocap": "xianfei/SysMocap",
            "https://github.com/steipete/codexbar": "steipete/CodexBar",
        }
        urls = list(cased) + [f"https://github.com/trending/repo{i}" for i in range(4)]
        novelty_db = tmp_path / "novelty.sqlite"

        rc = _run(_candidates(urls, now), novelty_db, vault, monkeypatch,
                  full_names=cased)
        assert rc == 0

        md_files = list(vault.rglob("*.md"))
        assert md_files, "无 .md 产物 — 前置条件不成立"
        md = md_files[0].read_text()

        for lower_url, full_name in cased.items():
            assert f"[{full_name}]({lower_url})" in md, (
                f"Markdown 标题未保留大小写：期望 [{full_name}]，实际见 "
                f"{[ln for ln in md.splitlines() if lower_url in ln]}"
            )

    def test_dedup_key_stays_lowercase_despite_display_case(
        self, tmp_path, vault, monkeypatch
    ):
        """显示层保留大小写不得污染去重键 —— 账本里必须仍是全小写 URL。"""
        now = datetime.now(UTC).isoformat()
        cased = {"https://github.com/id-software/quake": "id-Software/Quake"}
        urls = list(cased) + [f"https://github.com/trending/repo{i}" for i in range(6)]
        novelty_db = tmp_path / "novelty.sqlite"

        assert _run(_candidates(urls, now), novelty_db, vault, monkeypatch,
                    full_names=cased) == 0

        store = DedupStore(novelty_db)
        keys = [r[0] for r in store._conn.execute("SELECT normalized_url FROM seen_urls")]
        store.close()
        assert keys
        assert all(k == k.lower() for k in keys), f"去重键出现大写，键将分裂: {keys}"

    def test_display_name_falls_back_to_display_slug(self):
        """无 API full_name 时走 display_slug（不是回到 `.lower()` 的键切分）。"""
        from akb2.briefing.github.pipeline import _display_name

        assert _display_name({
            "repo_url": "https://github.com/id-Software/Quake",
            "repo_full_name": None,
        }) == "id-Software/Quake"
        assert _display_name({"repo_url": "", "repo_full_name": None}) == "unknown"

    def test_render_markdown_uses_full_name_directly(self):
        """单元层：_render_markdown 标题取 repo_full_name，不从小写 url 切。"""
        from akb2.briefing.github.pipeline import _render_markdown

        briefing = {
            "schema_version": "v1", "briefing_type": "github-curator",
            "generated_at": "2026-07-26T10:00:00Z", "date": "2026-07-26",
            "candidate_pool_size": 10, "active_curators_today": ["@test"],
            "fallback_layer_used": "layer1",
            "top_repos": [{
                "repo_url": "https://github.com/id-software/quake",
                "repo_full_name": "id-Software/Quake",
                "stars": 1000, "language": "C", "topic_tags": ["game"],
                "mention_count": 1, "mentioners": ["@a"],
                "first_mention_at": "2026-07-26T10:00:00Z",
                "score": 0.5, "summary": _SUMMARY, "use_case": _USE_CASE,
                "engagement_raw": 0,
            }],
        }
        md = _render_markdown(briefing)
        assert "## 1. [id-Software/Quake](https://github.com/id-software/quake)" in md
        assert "[id-software/quake]" not in md


class TestGH3WindowFloorVisibility:
    """GH-3：抬升窗口必须可见，下限必须由调用方链路声明（不硬绑 GitHub 常量）。"""

    def test_floor_raise_is_logged(self, capsys):
        """显式传入小于下限的窗口 → stderr 必须留痕（原则 4：失败/覆盖可见）。"""
        store = DedupStore(window_days=1)
        captured = capsys.readouterr()
        store.close()
        assert store.window_days == RECENCY_WINDOW_DAYS
        assert "1" in captured.err and str(RECENCY_WINDOW_DAYS) in captured.err
        assert "抬" in captured.err, f"抬升未打日志: {captured.err!r}"

    def test_no_log_when_window_not_raised(self, capsys):
        """未抬升时不打噪音（否则日志失去信号价值）。"""
        store = DedupStore(window_days=RECENCY_WINDOW_DAYS + 5)
        captured = capsys.readouterr()
        store.close()
        assert store.window_days == RECENCY_WINDOW_DAYS + 5
        assert "dedup_sqlite" not in captured.err

    def test_min_window_days_is_injectable(self):
        """每条链路声明自己的采集窗口 —— GitHub 常量不再暗中决定别人的窗口。"""
        short = DedupStore(window_days=2, min_window_days=2)
        assert short.window_days == 2
        assert short.min_window_days == 2
        short.close()

        raised = DedupStore(window_days=1, min_window_days=3)
        assert raised.window_days == 3
        raised.close()

    def test_default_min_window_preserves_github_behavior(self):
        """不传 min_window_days → 沿用 GitHub 采集窗口（既有行为不变）。"""
        store = DedupStore(window_days=1)
        assert store.min_window_days == RECENCY_WINDOW_DAYS
        store.close()

    def test_github_pipeline_store_floors_to_github_recency(
        self, tmp_path, vault, monkeypatch
    ):
        """生产接线：GitHub pipeline 建的 store 生效窗口 ≥ GitHub 采集窗口。"""
        now = datetime.now(UTC).isoformat()
        urls = [f"https://github.com/trending/repo{i}" for i in range(7)]
        novelty_db = tmp_path / "novelty.sqlite"

        real_init = DedupStore.__init__
        observed: list[int] = []

        def spy_init(self, *args, **kwargs):
            real_init(self, *args, **kwargs)
            observed.append(self.window_days)

        with patch.object(DedupStore, "__init__", spy_init):
            assert _run(_candidates(urls, now), novelty_db, vault, monkeypatch) == 0

        assert observed, "pipeline 未构造 DedupStore — 前置条件不成立"
        assert all(w >= RECENCY_WINDOW_DAYS for w in observed), observed
