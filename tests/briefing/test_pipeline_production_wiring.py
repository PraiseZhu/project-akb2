"""test_pipeline_production_wiring.py — 断言**生产调用路径**本身，而不是"函数被正确调用时行为对"。

本文件的每一条测试都跑真实入口 `pipeline.main()`，只替身外部依赖（intake / synthesis /
gate / translate / audit）。历史事故（`test_L1_url_dedup` 在测试里手工传 `db=db`、生产
`__main__.py` 从不传，79% 重复在 CI 全绿下跑了一个月）说明：断言"参数传对了行为就对"
的测试挡不住"生产从来没传这个参数"。

覆盖：
  S-0 DedupStore 的窗口下限必须由社媒**自己的采集窗口**决定，不借 GitHub 常量
  S-1 --dry-run-intake 绕过去重/时间窗，故其产出不得写入 Social（写隔离目录）
  S-3 audit 的 standard_warn_issues / audit_log_failed 必须被 pipeline 消费并落盘
  S-4 translate 总预算耗尽必须落盘可判读
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

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


def _synth(tweets: list[dict]) -> dict:
    return {
        "date": DATE,
        "generated_at": f"{DATE}T06:00:00Z",
        "source": {"name": "deepseek_synthesis", "collector_version": "1.0.0"},
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
    "standard_warn_issues": [],
    "llm_audit": "pass",
    "audit_degraded": False,
    "drift_score": 0.05,
    "accuracy_issues": [],
    "fix_loop_reason": "converged",
    "ts": "",
}


def _vault(root: Path) -> Path:
    (root / "Praise Space" / "AKB2.0" / "staging" / "candidate").mkdir(parents=True)
    (root / "Praise Space" / "AKB2.0" / "AI TRENDS" / "Social").mkdir(parents=True)
    return root


def _akb2(root: Path) -> Path:
    return root / "Praise Space" / "AKB2.0"


def _social(root: Path) -> Path:
    return _akb2(root) / "AI TRENDS" / "Social" / f"{DATE} Daily Briefing.md"


def _dry_run_out(root: Path) -> Path:
    return _akb2(root) / "staging" / "dry-run" / f"{DATE} Daily Briefing.md"


def _frontmatter(text: str) -> str:
    parts = text.split("---", 2)
    return parts[1] if len(parts) >= 3 else ""


def _run_pipeline(root, *, tweets=None, translate=None, verdict=None, argv_extra=()):
    """跑真实 `pipeline.main()`，只替身外部依赖。返回 main 的返回值。"""
    from akb2.briefing import pipeline as pl

    tweets = tweets if tweets is not None else [_tweet("100"), _tweet("200", "@ylecun", 0.8)]

    def default_translate(synth, **kwargs):
        return json.loads(json.dumps(synth))

    translate_fn = translate or default_translate

    with (
        patch.object(pl, "run_intake", return_value={"tweets": tweets}),
        patch.object(pl, "run_synthesis", side_effect=lambda scored, date: _synth(
            scored.get("tweets", []))),
        patch.object(pl, "run_gate", return_value="active"),
        patch("akb2.briefing.pipeline.translate_briefing", side_effect=translate_fn),
        patch.object(pl, "run_audit_fix_loop",
                     side_effect=lambda zh, en, **kw: (zh, dict(verdict or PASS_VERDICT))),
    ):
        return pl.main(["--date", DATE, "--vault-root", str(root), *argv_extra])


# ---------------------------------------------------------------------------
# S-0 去重窗口下限的归属：社媒自己的采集窗口，不是 GitHub 的 RECENCY_WINDOW_DAYS
# ---------------------------------------------------------------------------

def test_production_run_declares_social_intake_window_as_dedup_floor(tmp_path, monkeypatch):
    """真实 main() 打开账本时必须显式传社媒采集窗口作窗口下限。

    生产调用绑定：不 mock `apply_cross_day_dedup`，而是 spy `DedupStore` 构造参数——
    唯一让断言通过的方式是 pipeline 自己把 min_window_days 传下去。
    未修复前 pipeline 不传该参数，DedupStore 回落到 GitHub 的 RECENCY_WINDOW_DAYS=7，
    即 GitHub 常量暗中决定社媒去重窗口。
    """
    import akb2.briefing.github.dedup_sqlite as ds
    from akb2.briefing.pipeline import _INTAKE_WINDOW_DAYS_DEFAULT

    _vault(tmp_path)
    seen: list[dict] = []
    real_store = ds.DedupStore

    class SpyStore(real_store):
        def __init__(self, *args, **kwargs):
            seen.append(dict(kwargs))
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(ds, "DedupStore", SpyStore)
    assert _run_pipeline(tmp_path) == 0

    assert seen, "真实运行必须打开跨日去重账本"
    for kwargs in seen:
        assert "min_window_days" in kwargs, (
            "pipeline 未显式声明社媒采集窗口 → DedupStore 回落到 GitHub 的 "
            "RECENCY_WINDOW_DAYS，社媒去重窗口被别的链路的常量决定"
        )
        assert kwargs["min_window_days"] == _INTAKE_WINDOW_DAYS_DEFAULT, (
            f"窗口下限应为社媒采集窗口 {_INTAKE_WINDOW_DAYS_DEFAULT} 天，"
            f"实际 {kwargs['min_window_days']}"
        )
    assert ds.RECENCY_WINDOW_DAYS != _INTAKE_WINDOW_DAYS_DEFAULT, (
        "两条链路窗口若相等则本测试失去判别力（需改用其它可区分值）"
    )


def test_intake_window_env_override_flows_into_dedup_floor(tmp_path, monkeypatch):
    """采集窗口被 env 调大时，去重窗口下限必须跟着变（下限=本次运行的采集窗口）。"""
    import akb2.briefing.github.dedup_sqlite as ds

    _vault(tmp_path)
    monkeypatch.setenv("AKB2_INTAKE_WINDOW_DAYS", "11")
    seen: list[dict] = []
    real_store = ds.DedupStore

    class SpyStore(real_store):
        def __init__(self, *args, **kwargs):
            seen.append(dict(kwargs))
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(ds, "DedupStore", SpyStore)
    assert _run_pipeline(tmp_path) == 0
    assert seen and all(k.get("min_window_days") == 11 for k in seen), (
        f"采集窗口 11 天时下限应为 11，实际 {[k.get('min_window_days') for k in seen]}"
    )
    assert all(k.get("window_days", 0) >= 11 for k in seen), "去重窗口不得小于采集窗口"


# ---------------------------------------------------------------------------
# S-1 dry-run 产出不得写入 Social
# ---------------------------------------------------------------------------

def test_dry_run_intake_never_writes_social(tmp_path):
    """--dry-run-intake 跳过时间窗过滤与跨日去重，故其产出不得进 Social（写隔离目录）。

    未修复前 dry-run 照样 copy 到 Social（且推文不进账本）→ 次日真实运行可把同一批
    内容再发一遍。
    """
    _vault(tmp_path)
    assert _run_pipeline(tmp_path, argv_extra=("--dry-run-intake",)) == 0

    assert not _social(tmp_path).exists(), (
        "dry-run 产出不得写入 Social：它绕过了 _filter_recent 与跨日去重，"
        "且不进账本，次日真实运行会重复发布同一批内容"
    )
    assert _dry_run_out(tmp_path).exists(), "dry-run 产出应落在 staging/dry-run/ 隔离目录"
    fm = _frontmatter(_dry_run_out(tmp_path).read_text())
    assert "intake_source: dry_run" in fm
    assert "degraded: true" in fm


def test_dry_run_does_not_consume_material_for_real_run(tmp_path):
    """dry-run 不记账：随后真实运行同一批推文仍应正常发布（排练不消耗素材）。"""
    dry_root = _vault(tmp_path / "dry")
    real_root = _vault(tmp_path / "real")
    tweets = [_tweet("SAME1"), _tweet("SAME2", "@ylecun", 0.8)]

    assert _run_pipeline(dry_root, tweets=tweets, argv_extra=("--dry-run-intake",)) == 0
    assert _run_pipeline(real_root, tweets=tweets) == 0

    published = _social(real_root)
    assert published.exists(), "真实运行必须发布"
    assert "https://x.com/SAME1" in published.read_text(), (
        "dry-run 不应把素材吃掉（账本不得在 dry-run 时记账）"
    )


def test_real_run_still_writes_social(tmp_path):
    """反向守卫：隔离只针对 dry-run，真实运行仍必须写 Social。"""
    _vault(tmp_path)
    assert _run_pipeline(tmp_path) == 0
    assert _social(tmp_path).exists()
    assert not _dry_run_out(tmp_path).exists()


# ---------------------------------------------------------------------------
# S-3 audit 的两个悬空键必须被消费并落盘
# ---------------------------------------------------------------------------

def test_audit_standard_warn_issues_land_in_frontmatter(tmp_path):
    """audit 返回的 standard_warn_issues 必须进 frontmatter。

    未修复前该键生产并测试了，但 pipeline 一处都不消费（grep 只命中 audit.py 与测试）：
    产出层看到 audit_verdict: warn 却无从判读 warn 在哪。
    """
    _vault(tmp_path)
    warn_verdict = {
        **PASS_VERDICT,
        "verdict": "warn",
        "standard": "pass",
        "standard_warn_issues": [
            'executive_summary 术语表违规: Vibe Coding: 使用了禁用译法「氛围编码」',
            "tweets[1].text 英文散文泄漏：命中 5 个英文虚词 ['and', 'is', 'of']",
        ],
    }
    assert _run_pipeline(tmp_path, verdict=warn_verdict) == 0

    fm = _frontmatter(_social(tmp_path).read_text())
    assert "audit_warn_issues:" in fm, "warn 级确定性问题必须落盘（constitution 原则 4）"
    assert "氛围编码" in fm
    assert "英文散文泄漏" in fm
    # YAML 安全：issue 文案含冒号/引号/中括号，落盘后 frontmatter 仍必须可被解析
    import yaml  # noqa: PLC0415
    parsed = yaml.safe_load(fm)
    assert isinstance(parsed, dict), "含冒号的 issue 文案破坏了 frontmatter 结构"
    assert len(parsed["audit_warn_issues"]) == 2
    assert parsed["audit_warn_issue_count"] == 2


def test_warn_issue_list_is_truncated_with_visible_count(tmp_path):
    """warn issue 很多时列表截断，但总数必须写出来（截断本身可判读）。"""
    import yaml

    from akb2.briefing.pipeline import _FRONTMATTER_LIST_MAX

    _vault(tmp_path)
    many = [f"field{i} 术语表违规: 第 {i} 条" for i in range(_FRONTMATTER_LIST_MAX + 5)]
    verdict = {**PASS_VERDICT, "verdict": "warn", "standard_warn_issues": many}
    assert _run_pipeline(tmp_path, verdict=verdict) == 0

    parsed = yaml.safe_load(_frontmatter(_social(tmp_path).read_text()))
    assert parsed["audit_warn_issue_count"] == len(many), "总数必须是未截断的真实条数"
    assert len(parsed["audit_warn_issues"]) == _FRONTMATTER_LIST_MAX + 1
    assert "已截断" in parsed["audit_warn_issues"][-1]


def test_audit_log_write_failure_lands_in_frontmatter(tmp_path):
    """审计日志写盘失败（audit_log_failed）必须进 degradation_reasons，不只到 stderr。"""
    _vault(tmp_path)
    verdict = {**PASS_VERDICT, "audit_log_failed": True}
    assert _run_pipeline(tmp_path, verdict=verdict) == 0

    fm = _frontmatter(_social(tmp_path).read_text())
    assert "audit_log_write_failed" in fm, (
        "裁决记录丢失必须在产出层可判读（此前只有 stderr 一行，产出无迹）"
    )
    assert "degraded: true" in fm


def test_translate_failed_fields_land_in_frontmatter(tmp_path):
    """translate 回传的 translate_failed_fields 必须逐条进 frontmatter。"""
    _vault(tmp_path)

    def degraded_translate(synth, **kwargs):
        out = json.loads(json.dumps(synth))
        out["translate_degraded"] = True
        out["translate_failed_fields"] = ["tweets[3]", "tweets[5].core_excerpt"]
        return out

    assert _run_pipeline(tmp_path, translate=degraded_translate) == 0
    fm = _frontmatter(_social(tmp_path).read_text())
    assert "translate_failed_fields:" in fm
    assert "- tweets[3]" in fm
    assert "- tweets[5].core_excerpt" in fm


# ---------------------------------------------------------------------------
# S-4 翻译总预算耗尽必须落盘
# ---------------------------------------------------------------------------

def test_budget_exhausted_untranslated_tweets_must_not_block_publish(tmp_path):
    """生产事故 2026-09-02：translate 预算耗尽后未译条目仍留在展示集，
    真实 audit 因 core_excerpt/text 无中文判 fail，整份不入库。

    S-4 旧测试把 audit 替身成 pass，测不到这条路径。这里走真实
    run_audit_fix_loop + 代码标准检查；LLM 审计跳过。已译部分必须降级发布。
    """
    from akb2.briefing import pipeline as pl
    from akb2.briefing.audit_fix import run_audit_fix_loop

    _vault(tmp_path)
    tweets = [
        _tweet("100", score=0.95),
        _tweet("200", "@ylecun", 0.90),
        _tweet("300", "@karpathy", 0.85),
    ]

    def budget_exhausted_translate(synth, **kwargs):
        out = json.loads(json.dumps(synth))
        # 前两条已译（保留原文数字，避免 number_mismatch）；第 3 条预算耗尽留英文
        out["tweets"][0]["text"] = "推文 100 关于 AI 智能体。"
        out["tweets"][0]["zh_point"] = "记忆工具值得关注。"
        out["tweets"][1]["text"] = "推文 200 关于 AI 智能体。"
        out["tweets"][1]["zh_point"] = "持久记忆技术突破。"
        out["tweets"][2]["text"] = (
            "Brilliant paper on reducing reward hacking in agents. "
            "If you care about alignment this is required reading for the week."
        )
        out["tweets"][2]["core_excerpt"] = (
            "Brilliant paper on reducing reward hacking in agents."
        )
        out["translate_degraded"] = True
        out["translate_budget_exhausted"] = "time_budget"
        out["translate_failed_fields"] = ["tweets[2](budget_exhausted)"]
        return out

    def valid_synth(scored, date):
        items = scored.get("tweets", [])
        return {
            "date": DATE,
            "generated_at": f"{DATE}T06:00:00Z",
            "source": {"name": "deepseek_synthesis", "collector_version": "1.0.0"},
            "executive_summary": "今日 AI 智能体在记忆与对齐方面进展明显。",
            "scoring_summary": {"total_tweets": len(items), "scored_tweets": len(items)},
            "tweets": items,
            "focus_discussions": [
                {
                    "topic": "智能体记忆",
                    "summary": (
                        "多款记忆工具涌现，开始支撑跨会话召回。"
                        "对齐方向也有新论文进入视野。"
                        "基础设施层面正在收敛。"
                    ),
                    "supporting_tweet_ids": [t["id"] for t in items],
                }
            ],
        }

    with (
        patch.object(pl, "run_intake", return_value={"tweets": tweets}),
        patch.object(pl, "run_synthesis", side_effect=valid_synth),
        patch.object(pl, "run_gate", return_value="active"),
        patch("akb2.briefing.pipeline.translate_briefing", side_effect=budget_exhausted_translate),
        patch("akb2.briefing.audit._run_llm_audit", return_value={
            "llm_verdict": "pass", "accuracy_issues": [], "drift_score": 0.0,
        }),
        patch("akb2.briefing.translate._translate_snippet", return_value=""),
        patch("akb2.briefing.audit_fix._generate_zh_point", return_value=""),
        patch.object(pl, "run_audit_fix_loop", side_effect=run_audit_fix_loop),
    ):
        rc = pl.main(["--date", DATE, "--vault-root", str(tmp_path)])

    social = _social(tmp_path)
    assert rc == 0, f"已译部分应降级入库，实际 exit={rc} social_exists={social.exists()}"
    assert social.exists(), "预算耗尽不得把整份简报挡在 Social 外"
    body = social.read_text()
    assert "Brilliant paper on reducing reward hacking" not in body
    fm = _frontmatter(body)
    assert "translate_budget_exhausted: time_budget" in fm
    assert "translate_status: degraded" in fm


def test_translate_budget_exhausted_lands_in_frontmatter(tmp_path):
    """translate 报总预算耗尽时，产出必须自报原因 + 点名未翻译条目。"""
    _vault(tmp_path)

    def budget_exhausted_translate(synth, **kwargs):
        out = json.loads(json.dumps(synth))
        out["translate_degraded"] = True
        out["translate_budget_exhausted"] = "time_budget"
        out["translate_failed_fields"] = ["tweets[7](budget_exhausted)"]
        return out

    assert _run_pipeline(tmp_path, translate=budget_exhausted_translate) == 0
    fm = _frontmatter(_social(tmp_path).read_text())
    assert "translate_budget_exhausted: time_budget" in fm, (
        "预算耗尽必须可判读，否则与'模型逐条失败'在产出层长得一样"
    )
    assert "translate_budget_exhausted:time_budget" in fm  # degradation_reasons 里那条
    assert "- tweets[7](budget_exhausted)" in fm
    assert "translate_status: degraded" in fm


def test_no_budget_marker_when_budget_not_hit(tmp_path):
    """反向守卫：未触达预算时不得出现预算标记（避免恒真断言）。"""
    _vault(tmp_path)
    assert _run_pipeline(tmp_path) == 0
    fm = _frontmatter(_social(tmp_path).read_text())
    assert "translate_budget_exhausted" not in fm
    assert "audit_log_write_failed" not in fm
    assert "audit_warn_issues" not in fm


@pytest.mark.parametrize("bad", ["a: b: c", 'x "quoted" y', "line1\nline2"])
def test_warn_issue_text_never_breaks_frontmatter(tmp_path, bad):
    """warn issue 文案里的冒号/引号/换行都不得破坏 frontmatter 结构。"""
    import yaml

    _vault(tmp_path)
    verdict = {**PASS_VERDICT, "verdict": "warn", "standard_warn_issues": [bad]}
    assert _run_pipeline(tmp_path, verdict=verdict) == 0
    parsed = yaml.safe_load(_frontmatter(_social(tmp_path).read_text()))
    assert isinstance(parsed, dict)
    assert len(parsed["audit_warn_issues"]) == 1
