"""退出码诚实性 + synthesis 输出质量重生成 —— GitHub briefing pipeline。

锁死两条不变式：

L6-1「未交付即非零」
    2026-06-26 ~ 06-29 连续 4 天 logs/github-briefing-stderr.log 记 `exit=0`，同期
    stdout 是 `SKIPPED (enrich_degraded: 18/18 enrich calls failed)`，vault
    `AI TRENDS/GitHub/` 这 4 天零文件 —— 看退出码的监控（含 launchctl 的
    LastExitStatus）把 4 天零交付读成了成功。而同为 SKIPPED 类的
    schema_validation_failed 当时是 exit=1，同类分支退出码不一致。
    本文件对**每一个** skip / block / fail 分支参数化断言 `rc != 0` 且 vault 零写入。

L6-2「LLM 输出质量失败必须有界重生成」
    24 天内 6 次 exit=1，5 次是输出质量问题（3 次 JSON 写坏、2 次 schema 不过），
    而 MAX_RETRIES=3 只覆盖 HTTP 层 —— 这两类此前零自动重试。
    本文件断言重生成会触发、有界（不越 MAX_SYNTHESIS_REGENERATIONS）、
    且耗尽后仍 fail-closed 不写 vault。

断言绑的都是**可观测行为**（退出码 / vault 文件在不在 / stdout 上的 REGENERATE 行），
不是"函数被调到了"。
"""

from __future__ import annotations

import inspect
import io
import json
import re
from datetime import UTC, date, datetime
from unittest.mock import patch
from urllib.error import HTTPError

import pytest

from akb2.briefing.github import pipeline as gh_pipeline
from akb2.briefing.github.intake import Candidate
from akb2.briefing.github.pipeline import run_pipeline

# ── 共用 fixture 数据 ────────────────────────────────────────────────────────

_OUT_SUBDIR = "Praise Space/AKB2.0/AI TRENDS/GitHub"
# summary/use_case 必须含 CJK：schema 的 pattern 要求至少一个中日韩字符，
# 英文占位会被判 schema_validation_failed，测不到本文件真正想测的分支。
_GOOD_SUMMARY = "这是一个用于测试的 AI 仓库，覆盖机器学习工作流与流水线校验。"
_GOOD_USE_CASE = "正向场景：搭建 AI 流水线时使用。反向场景：生产环境不要直接依赖。"

def _build_truncated_json() -> str:
    """逐字复刻 07-22 生产失败的形状（logs/github-briefing-stdout.log）：

        FAILED (synthesis_failed: Unterminated string starting at: line 44 column 16 ...)

    行号/列号刻意对齐真实故障（44 行、第 44 行第 16 列起的字符串未闭合），
    这样回归锁与生产日志能逐字对读，而不只是"某个坏 JSON"。
    """
    lines = ["["]
    for i in range(1, 15):  # 14 条完整条目 → 占据第 2..43 行
        lines.append(f'  {{"repo_url": "https://github.com/o/r{i}",')
        lines.append('   "summary": "完整的中文摘要，长度足以通过 schema 校验。",')
        lines.append('   "use_case": "正向：测试用。反向：勿依赖。"},')
    prefix = '  {"summary":  '  # 长度 15 → 开引号正好落在第 16 列
    assert len(prefix) == 15
    lines.append(prefix + '"这是被截断的产出')  # 第 44 行：字符串未闭合
    return "\n".join(lines)


_TRUNCATED_JSON = _build_truncated_json()


def _candidates(n: int) -> list[Candidate]:
    now = datetime.now(UTC).isoformat()
    return [
        Candidate(f"https://github.com/trending/repo{i}", "@github_trending", None, now, None)
        for i in range(n)
    ]


def _enrich_ok(url: str) -> dict:
    h = sum(ord(c) for c in url)
    return {
        "repo_url": url, "stars": 500 + h % 500, "language": "Python",
        "topic_tags": ["ai"], "created_at": "2024-01-01T00:00:00Z",
        "readme_excerpt": "x" * 100, "commits_7d": 5, "enrich_ok": True,
    }


def _enrich_all_fail(url: str) -> dict:
    """enrich 全失败 —— 06-26~29 的真实形状（18/18 enrich calls failed）。"""
    return {"repo_url": url, "enrich_ok": False}


def _synth_ok(repos, api_key=None, *, mock=False):
    return {"top_repos": [
        {**r, "summary": _GOOD_SUMMARY, "use_case": _GOOD_USE_CASE} for r in repos
    ]}


def _synth_short_summary(repos, api_key=None, *, mock=False):
    """schema_validation_failed 的真实形状（07-03/07-10：`'…' is too short` ×N）。"""
    return {"top_repos": [{**r, "summary": "短", "use_case": "短"} for r in repos]}


def _synth_truncated_json(repos, api_key=None, *, mock=False):
    """JSON 写坏 —— 复刻 07-22/25/28：synthesize 内部第二次 json.loads 冒泡出的异常。"""
    json.loads(_TRUNCATED_JSON)  # 抛真的 json.JSONDecodeError，不手工造假异常
    raise AssertionError("unreachable")  # pragma: no cover


class _Vault:
    """一次性 vault：既是 VAULT_ROOT，也是"有没有当日文件"的观察点。

    同时把诊断落点隔离到 tmp_path：`_PROJECT_ROOT` 是**模块导入时**从 env 定下的常量，
    在进程内跑 `run_pipeline` 时改 `PROJECT_ROOT` env **不生效** ——
    不 setattr 的话 briefing-skipped/failed.json 会写到真实仓库根上去。
    （沿用 tests/briefing/github/test_production_wiring.py 的既有做法。）
    """

    def __init__(self, tmp_path, monkeypatch):
        self.root = tmp_path / "vault"
        self.out_dir = self.root / _OUT_SUBDIR
        self.out_dir.mkdir(parents=True)
        monkeypatch.setenv("VAULT_ROOT", str(self.root))
        monkeypatch.setenv("AKB2_NOVELTY_DB", str(tmp_path / "novelty.sqlite"))
        monkeypatch.setenv("AKB2_STAR_HISTORY_DB", str(tmp_path / "star.sqlite"))
        monkeypatch.setenv("PROJECT_ROOT", str(tmp_path))
        monkeypatch.setattr(gh_pipeline, "_PROJECT_ROOT", tmp_path)

    @property
    def today_md(self):
        return self.out_dir / f"{date.today().isoformat()} GitHub Briefing.md"

    def written_files(self) -> list:
        return sorted(p.name for p in self.out_dir.iterdir())


# ── SC-1：每个 SKIPPED / BLOCKED / FAILED 分支的退出码 ≠ 0 ────────────────────

# (分支名, 候选数, enrich 实现, synthesize 实现, stdout 里应出现的 reason 片段)
_NOT_DELIVERED_BRANCHES = [
    # 06-26~29 的真实死因：过半 enrich 失败 → 当时 exit=0（本文件的主回归锁）
    ("enrich_degraded", 7, _enrich_all_fail, _synth_ok, "enrich_degraded"),
    # 07-03 / 07-10 的真实死因：schema 校验不过
    ("schema_validation_failed", 7, _enrich_ok, _synth_short_summary,
     "schema_validation_failed"),
    # 07-22 / 07-25 / 07-28 的真实死因：JSON 写坏（重生成耗尽后仍失败）
    ("synthesis_failed", 7, _enrich_ok, _synth_truncated_json, "synthesis_failed"),
    # 候选不足：aggregate 后 < _MIN_REPOS_FOR_BRIEFING
    ("too_few_aggregated_repos", 3, _enrich_ok, _synth_ok, "too_few_aggregated_repos"),
]


@pytest.mark.parametrize(
    ("branch", "n_cands", "enrich_impl", "synth_impl", "reason_fragment"),
    _NOT_DELIVERED_BRANCHES,
    ids=[b[0] for b in _NOT_DELIVERED_BRANCHES],
)
def test_not_delivered_branch_exits_nonzero_and_writes_nothing(
    branch, n_cands, enrich_impl, synth_impl, reason_fragment,
    tmp_path, monkeypatch, capsys,
):
    """未交付分支：退出码 ≠ 0 且 vault 零写入，reason 在 stdout 可判读。"""
    vault = _Vault(tmp_path, monkeypatch)

    with (
        patch("akb2.briefing.github.pipeline._real_intake",
              return_value=(_candidates(n_cands), "layer2", {})),
        patch("akb2.briefing.github.pipeline.enrich", side_effect=enrich_impl),
        patch("akb2.briefing.github.pipeline.synthesize", side_effect=synth_impl),
    ):
        rc = run_pipeline(mock=False)

    out = capsys.readouterr().out
    assert rc != 0, f"{branch}: 未交付却返回 {rc} —— 退出码撒谎（stdout: {out}）"
    assert rc == gh_pipeline.EXIT_NOT_DELIVERED, f"{branch}: 期望具名码, 实得 {rc}"
    assert vault.written_files() == [], f"{branch}: vault 不应有产物, 实得 {vault.written_files()}"
    assert reason_fragment in out, f"{branch}: stdout 缺 reason={reason_fragment}: {out}"


def test_vault_root_missing_exits_nonzero(tmp_path, monkeypatch):
    """VAULT_ROOT 缺失/相对路径 —— 最早的 skip 分支同样未交付即非零。"""
    monkeypatch.setenv("VAULT_ROOT", "")
    monkeypatch.setattr(gh_pipeline, "_PROJECT_ROOT", tmp_path)
    assert run_pipeline(mock=False) == gh_pipeline.EXIT_NOT_DELIVERED


def test_intake_fixture_fallback_branch_exit_code_is_nonzero():
    """social 侧 intake_fixture_fallback（07-27/07-28，exit=3）同样落在非零集合内。

    该分支在 briefing/pipeline.py（social），这里只静态断言它的具名码非零 ——
    行为级断言在 tests/briefing/test_pipeline_exit_codes.py。
    """
    from akb2.briefing import pipeline as social_pipeline

    assert social_pipeline.EXIT_INTAKE_FIXTURE_BLOCKED != 0


def test_delivered_path_exits_zero_and_writes_file(tmp_path, monkeypatch):
    """反向锚点：真交付时 rc == 0 且当日文件存在 —— 否则上面的"非零"断言没有意义
    （一个恒返回非零的 pipeline 也能让 SC-1 全绿）。"""
    vault = _Vault(tmp_path, monkeypatch)

    with (
        patch("akb2.briefing.github.pipeline._real_intake",
              return_value=(_candidates(7), "layer2", {})),
        patch("akb2.briefing.github.pipeline.enrich", side_effect=_enrich_ok),
        patch("akb2.briefing.github.pipeline.synthesize", side_effect=_synth_ok),
    ):
        rc = run_pipeline(mock=False)

    assert rc == gh_pipeline.EXIT_DELIVERED
    assert vault.today_md.exists()


# ── SC-2：回放 06-26~29 的 enrich_degraded（18/18 全失败）────────────────────

def test_enrich_degraded_18_of_18_replays_four_day_outage(tmp_path, monkeypatch, capsys):
    """06-26~29 回归锁：18 个候选 enrich 全失败 → 非零 + vault 零写入。

    形状照抄生产 stdout：`enrich_degraded: 18/18 enrich calls failed`。
    """
    vault = _Vault(tmp_path, monkeypatch)

    with (
        patch("akb2.briefing.github.pipeline._real_intake",
              return_value=(_candidates(18), "layer2", {})),
        patch("akb2.briefing.github.pipeline.enrich", side_effect=_enrich_all_fail),
    ):
        rc = run_pipeline(mock=False)

    out = capsys.readouterr().out
    assert rc != 0, f"4 天零交付被读成成功的那个 bug 回来了: rc={rc}"
    assert "enrich_degraded: 18/18 enrich calls failed" in out, out
    assert vault.written_files() == [], vault.written_files()


# ── SC-3：码位不与保留码冲突 + 无裸字面量 ────────────────────────────────────

def test_exit_codes_do_not_collide_with_reserved():
    """具名退出码 ∩ {124, 125} == ∅。

    124 = scripts/run_with_deadline.py 的 deadline 码；125 = backstop 主动放弃码。
    撞上任何一个，backstop / launchd 的归因就会把"pipeline 自己失败"读成
    "超时"或"backstop 放弃"。
    """
    assert gh_pipeline.RESERVED_EXIT_CODES == frozenset({124, 125})
    assert gh_pipeline.EXIT_CODES & gh_pipeline.RESERVED_EXIT_CODES == frozenset()

    from akb2.briefing import pipeline as social_pipeline
    assert social_pipeline.EXIT_CODES & social_pipeline.RESERVED_EXIT_CODES == frozenset()


def test_run_pipeline_has_no_bare_literal_exit_codes():
    """run_pipeline 里不得出现 `return 1` / `return 3` 之类裸字面量（宪法 P4 具名）。"""
    source = inspect.getsource(gh_pipeline.run_pipeline)
    bare = re.findall(r"^\s*return\s+(-?\d+)\s*$", source, flags=re.MULTILINE)
    assert bare == [], f"run_pipeline 仍有裸字面量退出码: {bare}"
    assert "EXIT_NOT_DELIVERED" in source and "EXIT_DELIVERED" in source


def test_every_named_exit_code_is_defined_and_nonzero_means_not_delivered():
    """EXIT_CODES 里除 EXIT_DELIVERED 外全部非零（"未交付即非零"的静态形态）。"""
    assert gh_pipeline.EXIT_DELIVERED == 0
    non_delivered = gh_pipeline.EXIT_CODES - {gh_pipeline.EXIT_DELIVERED}
    assert non_delivered, "至少要有一个未交付码位"
    assert all(c != 0 for c in non_delivered)


# ── SC-4 / SC-5 / SC-6：synthesis 输出质量重生成 ─────────────────────────────

_REGEN_RE = re.compile(r"REGENERATE attempt=(\d+)/(\d+) reason=(\S+)")


def _regen_lines(stdout: str) -> list[tuple[str, str, str]]:
    return _REGEN_RE.findall(stdout)


def test_json_truncated_then_valid_regenerates_and_delivers(tmp_path, monkeypatch, capsys):
    """SC-4：第一轮 JSON 写坏（07-22 形状）→ REGENERATE attempt=1/2 → 第二轮合法 → 交付。"""
    vault = _Vault(tmp_path, monkeypatch)
    calls = {"n": 0}

    def synth(repos, api_key=None, *, mock=False):
        calls["n"] += 1
        if calls["n"] == 1:
            return _synth_truncated_json(repos)
        return _synth_ok(repos)

    with (
        patch("akb2.briefing.github.pipeline._real_intake",
              return_value=(_candidates(7), "layer2", {})),
        patch("akb2.briefing.github.pipeline.enrich", side_effect=_enrich_ok),
        patch("akb2.briefing.github.pipeline.synthesize", side_effect=synth),
    ):
        rc = run_pipeline(mock=False)

    out = capsys.readouterr().out
    assert _regen_lines(out) == [("1", "2", "json_truncated")], out
    assert rc == gh_pipeline.EXIT_DELIVERED, f"重生成成功后必须交付: rc={rc}\n{out}"
    assert vault.today_md.exists(), "重生成成功 → vault 必须有当日文件"
    assert calls["n"] == 2, f"应恰好调 2 次 synthesize（1 坏 + 1 好），实得 {calls['n']}"


def test_json_truncated_always_exhausts_budget_then_fails_closed(
    tmp_path, monkeypatch, capsys,
):
    """SC-5：每轮都 JSON 写坏 → attempt=1/2、2/2，**不出现 3/2** → 非零 + vault 零写入。"""
    vault = _Vault(tmp_path, monkeypatch)
    calls = {"n": 0}

    def synth(repos, api_key=None, *, mock=False):
        calls["n"] += 1
        return _synth_truncated_json(repos)

    with (
        patch("akb2.briefing.github.pipeline._real_intake",
              return_value=(_candidates(7), "layer2", {})),
        patch("akb2.briefing.github.pipeline.enrich", side_effect=_enrich_ok),
        patch("akb2.briefing.github.pipeline.synthesize", side_effect=synth),
    ):
        rc = run_pipeline(mock=False)

    out = capsys.readouterr().out
    assert _regen_lines(out) == [("1", "2", "json_truncated"), ("2", "2", "json_truncated")], out
    assert "attempt=3/2" not in out, f"重生成越界（预算 2 次）: {out}"
    # 预算 = 具名常量，不是散落的 2
    assert calls["n"] == gh_pipeline.MAX_SYNTHESIS_REGENERATIONS + 1, (
        f"synthesize 调用次数应为 1 + MAX_SYNTHESIS_REGENERATIONS, 实得 {calls['n']}"
    )
    assert rc != 0, "重生成耗尽 → 未交付 → 必须非零"
    assert vault.written_files() == [], "fail-closed：耗尽后不得为了'有产出'而降级写入"
    assert "synthesis_failed" in out, out


def test_schema_invalid_triggers_regeneration_then_fails_closed(
    tmp_path, monkeypatch, capsys,
):
    """SC-6：schema 不过（07-03 的 `too short` 形状）同样重生成；耗尽后非零 + 零写入。"""
    vault = _Vault(tmp_path, monkeypatch)
    calls = {"n": 0}

    def synth(repos, api_key=None, *, mock=False):
        calls["n"] += 1
        return _synth_short_summary(repos)

    with (
        patch("akb2.briefing.github.pipeline._real_intake",
              return_value=(_candidates(7), "layer2", {})),
        patch("akb2.briefing.github.pipeline.enrich", side_effect=_enrich_ok),
        patch("akb2.briefing.github.pipeline.synthesize", side_effect=synth),
    ):
        rc = run_pipeline(mock=False)

    out = capsys.readouterr().out
    assert _regen_lines(out) == [("1", "2", "schema_invalid"), ("2", "2", "schema_invalid")], out
    assert calls["n"] == gh_pipeline.MAX_SYNTHESIS_REGENERATIONS + 1, calls["n"]
    assert rc != 0
    assert vault.written_files() == []
    assert "schema_validation_failed" in out, out
    assert "is too short" in out, f"应保留 schema 原始 reason 供归因: {out}"


def test_schema_invalid_then_valid_regenerates_and_delivers(tmp_path, monkeypatch, capsys):
    """schema 不过 → 重生成一次拿到合法产出 → 交付（07-03/10 本可自愈的证明）。"""
    vault = _Vault(tmp_path, monkeypatch)
    calls = {"n": 0}

    def synth(repos, api_key=None, *, mock=False):
        calls["n"] += 1
        return _synth_short_summary(repos) if calls["n"] == 1 else _synth_ok(repos)

    with (
        patch("akb2.briefing.github.pipeline._real_intake",
              return_value=(_candidates(7), "layer2", {})),
        patch("akb2.briefing.github.pipeline.enrich", side_effect=_enrich_ok),
        patch("akb2.briefing.github.pipeline.synthesize", side_effect=synth),
    ):
        rc = run_pipeline(mock=False)

    out = capsys.readouterr().out
    assert _regen_lines(out) == [("1", "2", "schema_invalid")], out
    assert rc == gh_pipeline.EXIT_DELIVERED
    assert vault.today_md.exists()


# ── 重生成的**排除**类别：不该重试的一律不重试（省额度 + 不掩盖真因）──────────

def test_budget_truncation_is_not_regenerated(tmp_path, monkeypatch, capsys):
    """SynthesisTruncatedError（finish_reason=length）不重生成 —— 同上限重跑必然截在同一处。"""
    from akb2.briefing.github.synthesis import SynthesisTruncatedError

    vault = _Vault(tmp_path, monkeypatch)
    calls = {"n": 0}

    def synth(repos, api_key=None, *, mock=False):
        calls["n"] += 1
        raise SynthesisTruncatedError("LLM 输出撞 max_tokens 上限被截断（finish_reason=length）")

    with (
        patch("akb2.briefing.github.pipeline._real_intake",
              return_value=(_candidates(7), "layer2", {})),
        patch("akb2.briefing.github.pipeline.enrich", side_effect=_enrich_ok),
        patch("akb2.briefing.github.pipeline.synthesize", side_effect=synth),
    ):
        rc = run_pipeline(mock=False)

    out = capsys.readouterr().out
    assert calls["n"] == 1, f"预算撞顶不该重试, 实际调了 {calls['n']} 次（白烧额度）"
    assert _regen_lines(out) == [], out
    assert rc != 0
    assert vault.written_files() == []


def test_rate_limit_429_is_not_regenerated(tmp_path, monkeypatch, capsys):
    """429 / 预算耗尽不重生成 —— 重跑只会继续撞同一个已耗尽的配额（08-02 的真实死因）。"""
    vault = _Vault(tmp_path, monkeypatch)
    calls = {"n": 0}

    def synth(repos, api_key=None, *, mock=False):
        calls["n"] += 1
        raise RuntimeError(
            "DeepSeek API failed after 3 retries: HTTP Error 429: Too Many Requests"
        )

    with (
        patch("akb2.briefing.github.pipeline._real_intake",
              return_value=(_candidates(7), "layer2", {})),
        patch("akb2.briefing.github.pipeline.enrich", side_effect=_enrich_ok),
        patch("akb2.briefing.github.pipeline.synthesize", side_effect=synth),
    ):
        rc = run_pipeline(mock=False)

    out = capsys.readouterr().out
    assert calls["n"] == 1, f"429 不该重试, 实际调了 {calls['n']} 次"
    assert _regen_lines(out) == [], out
    assert rc != 0
    assert vault.written_files() == []
    assert "HTTP Error 429" in out, f"429 真因必须保留在 reason 里: {out}"


def test_regenerable_classifier_boundaries():
    """分类器的边界用类型判定，不靠匹配错误文本（文本会随 provider 变）。"""
    from akb2.briefing.github.synthesis import SynthesisTruncatedError

    try:
        json.loads(_TRUNCATED_JSON)
    except json.JSONDecodeError as exc:
        assert gh_pipeline._is_regenerable_synthesis_error(exc) is True
        # 逐字对齐 07-22 生产日志，回归锁可与故障记录对读
        assert str(exc).startswith("Unterminated string starting at: line 44 column 16"), str(exc)
    else:  # pragma: no cover
        raise AssertionError("fixture 应当是坏 JSON")

    assert gh_pipeline._is_regenerable_synthesis_error(SynthesisTruncatedError("x")) is False
    assert gh_pipeline._is_regenerable_synthesis_error(RuntimeError("HTTP Error 429")) is False
    assert gh_pipeline._is_regenerable_synthesis_error(ValueError("empty grounding")) is False


# ── SC-9：诊断落盘足以支撑告警归因（L6-5）─────────────────────────────────────
#
# 事故（2026-08-03 早上）：告警只说「github 缺失（期望路径: …）」，看不出真因是预算耗尽。
# 排查一度归因为"没写诊断文件"——**不是**。`briefing-failed.json` 确实写出来了且含 429
# 原文。卡住的是**日期键**：feishu_sync/blocked.py:blocking_reason() 要求诊断日期 ==
# 告警日期，而 github 诊断没有 `date` 字段 → 读取侧退回 `generated_at[:10]`（UTC 日）
# → 07:30 CST = 前一天 23:30 UTC → UTC 日 ≠ 本地日 → 校验不过 → 返回 None。
# 这一节的断言直接绑「告警拿不拿得到真因」，而不只是「文件在不在」。


def _read_diag(tmp_path, name: str) -> dict:
    path = tmp_path / name
    assert path.exists(), f"未交付却没留下 {name} —— 告警将无素材可用"
    return json.loads(path.read_text(encoding="utf-8"))


def _budget_exceeded_error():
    """08-02 的真实死因形状：429 + UTC 日预算打穿，携带契约 2 诊断字段。

    直接用 synthesis 层的真类型与真解析器（`common/http_retry` 的
    ProviderErrorDiagnosis），不手工造假 diagnosis —— 否则测的是我自己的假数据形态。
    """
    from akb2.briefing.github.synthesis import SynthesisAPIError
    from akb2.common.http_retry import classify_provider_error

    raw = (
        "ExceededBudget: exceeded UTC daily cap. "
        "Date=2026-08-01, Spend=2701.23, DailyCap=2700.0"
    )
    diagnosis = classify_provider_error(429, raw)
    return SynthesisAPIError(
        f"DeepSeek API failed after 3 retries: HTTP Error 429: Too Many Requests ({raw})",
        diagnosis=diagnosis, model="deepseek-v4-pro", exhausted=True,
    )


def test_synthesis_budget_exhausted_writes_attributable_diagnostic(
    tmp_path, monkeypatch, capsys,
):
    """SC-9 主锁：429 预算耗尽 → briefing-failed.json 存在，且含 failure_class + reason，
    reason/detail 里能看出是**预算耗尽**（不是泛泛的"失败"）。"""
    vault = _Vault(tmp_path, monkeypatch)
    err = _budget_exceeded_error()

    def synth(repos, api_key=None, *, mock=False):
        raise err

    with (
        patch("akb2.briefing.github.pipeline._real_intake",
              return_value=(_candidates(7), "layer2", {})),
        patch("akb2.briefing.github.pipeline.enrich", side_effect=_enrich_ok),
        patch("akb2.briefing.github.pipeline.synthesize", side_effect=synth),
    ):
        rc = run_pipeline(mock=False)

    assert rc != 0
    assert vault.written_files() == []
    diag = _read_diag(tmp_path, "briefing-failed.json")

    assert diag["status"] == "failed"
    assert diag["stage"] == gh_pipeline.FAILURE_STAGE_SYNTHESIZE
    assert diag["failure_class"] == gh_pipeline.FAILURE_CLASS_LLM_PROVIDER
    # reason 保留原始错误文本 —— 告警要能看出是 429/预算，而非只说"synthesis 失败"
    assert "synthesis_failed" in diag["reason"]
    assert "429" in diag["reason"]
    assert "ExceededBudget" in diag["reason"] or "daily cap" in diag["reason"]
    # 契约 2 字段（COMMON.md）：由 synthesis 层解析，pipeline 只负责落盘
    pd = diag["provider_diagnosis"]
    assert pd["error_type"] == "budget_exceeded"
    assert pd["budget_scope"] == "utc_daily"
    assert pd["provider_code"] == 429
    assert pd["spend"] == 2701.23
    assert pd["cap"] == 2700.0
    assert pd["budget_date"] == "2026-08-01"
    # detail = 告警可直接念的下一步，必须说清"等 UTC 日重置"
    assert diag["remediable"] is True
    assert "UTC" in diag["detail"] and "重置" in diag["detail"]
    # stdout 也要留痕（看日志的人和看告警的人拿到同一个真因）
    assert "429" in capsys.readouterr().out


def test_diagnostic_date_is_local_day_matching_vault_filename(tmp_path, monkeypatch):
    """SC-9 关键字段：`date` 必须是**本地日**，与 vault 文件名同源。

    这条就是 2026-08-03 告警说不清根因的真正修复点：读取侧优先用 `date`，
    只有缺失时才退回 `generated_at[:10]`（UTC 日）——而 UTC 日在 05:30/07:30
    两个跑批时刻必然比本地日**早一天**，日期校验必然不过。
    """
    vault = _Vault(tmp_path, monkeypatch)

    def synth(repos, api_key=None, *, mock=False):
        raise _budget_exceeded_error()

    with (
        patch("akb2.briefing.github.pipeline._real_intake",
              return_value=(_candidates(7), "layer2", {})),
        patch("akb2.briefing.github.pipeline.enrich", side_effect=_enrich_ok),
        patch("akb2.briefing.github.pipeline.synthesize", side_effect=synth),
    ):
        run_pipeline(mock=False)

    diag = _read_diag(tmp_path, "briefing-failed.json")
    local_today = date.today().isoformat()
    assert diag["date"] == local_today, (
        f"诊断 date={diag['date']} 必须等于本地日 {local_today}"
    )
    # 同源自证：vault 当日文件名用的是同一个本地日
    assert vault.today_md.name.startswith(local_today)
    # 反向锚点：UTC 日与本地日不一致时，绝不能退回用 UTC 日当 date
    utc_day = diag["generated_at"][:10]
    if utc_day != local_today:
        assert diag["date"] != utc_day, "date 落成了 UTC 日 —— 告警又会对不上"


def test_alert_reader_can_attribute_root_cause_in_utc_skew_window(tmp_path, monkeypatch):
    """SC-9 最终锁：把落盘诊断喂给**真实告警读取侧**，在 UTC/本地日错开的窗口里仍能归因。

    为什么要人为把 `generated_at` 挪到前一天：真实故障发生在 07:30 CST = 前一天
    23:30 UTC，只有这个窗口里 UTC 日 ≠ 本地日、日期校验才会不过。若直接用"现在"跑，
    白天时段两个日期恰好相同，读取侧靠 `generated_at` 兜底也能过 —— 那这条锁在
    白天恒绿、只在凌晨才失败，等于没锁住（本用例最初就是这个毛病）。
    这里把生产时刻的形状写死，让锁与时钟无关。

    只读消费 feishu_sync/blocked.py（读取侧是别的 lane 的地盘，只调用不修改）。
    """
    import datetime as _dt

    from akb2.feishu_sync import blocked

    vault = _Vault(tmp_path, monkeypatch)

    def synth(repos, api_key=None, *, mock=False):
        raise _budget_exceeded_error()

    with (
        patch("akb2.briefing.github.pipeline._real_intake",
              return_value=(_candidates(7), "layer2", {})),
        patch("akb2.briefing.github.pipeline.enrich", side_effect=_enrich_ok),
        patch("akb2.briefing.github.pipeline.synthesize", side_effect=synth),
    ):
        run_pipeline(mock=False)

    assert vault.written_files() == []
    diag_path = tmp_path / "briefing-failed.json"
    payload = json.loads(diag_path.read_text(encoding="utf-8"))

    # 复刻 07:30 CST 跑批的真实形状：generated_at 落在**前一天** 23:30 UTC
    local_today = date.today().isoformat()
    prev_utc_day = (date.today() - _dt.timedelta(days=1)).isoformat()
    payload["generated_at"] = f"{prev_utc_day}T23:30:51.037700+00:00"
    diag_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    assert payload["generated_at"][:10] != local_today, "前提：UTC 日必须与本地日错开"

    reason = blocked.blocking_reason("github", local_today, repo_root=tmp_path)
    assert reason is not None, (
        "告警读取侧仍拿不到真因 —— 这正是 2026-08-03 早上只报「文件缺失」的那个 bug"
    )
    assert "429" in reason or "budget" in reason.lower(), reason
    assert "UTC" in reason and "重置" in reason, f"告警文案必须给出可执行下一步: {reason}"

    # 反证：去掉 `date` 字段后读取侧只能退回 UTC 日 → 日期校验不过 → 丢失真因。
    # 这就是修复前的原状，也说明 `date` 字段正是那把钥匙。
    payload.pop("date")
    diag_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    assert blocked.blocking_reason("github", local_today, repo_root=tmp_path) is None, (
        "缺 date 时读取侧居然还能归因 —— 说明本用例没复现出 UTC/本地日错开的窗口"
    )


@pytest.mark.parametrize(
    ("branch", "n_cands", "enrich_impl", "synth_impl", "diag_file", "stage", "failure_class"),
    [
        ("vault_root_ok_but_too_few", 3, _enrich_ok, _synth_ok, "briefing-skipped.json",
         gh_pipeline.FAILURE_STAGE_INTAKE, gh_pipeline.FAILURE_CLASS_UPSTREAM_INTAKE),
        ("enrich_degraded", 7, _enrich_all_fail, _synth_ok, "briefing-skipped.json",
         gh_pipeline.FAILURE_STAGE_ENRICH, gh_pipeline.FAILURE_CLASS_ENRICH_DEGRADED),
        ("schema_validation_failed", 7, _enrich_ok, _synth_short_summary,
         "briefing-skipped.json",
         gh_pipeline.FAILURE_STAGE_VALIDATE, gh_pipeline.FAILURE_CLASS_LLM_OUTPUT_QUALITY),
        ("synthesis_json_truncated", 7, _enrich_ok, _synth_truncated_json,
         "briefing-failed.json",
         gh_pipeline.FAILURE_STAGE_SYNTHESIZE, gh_pipeline.FAILURE_CLASS_LLM_OUTPUT_QUALITY),
    ],
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_every_failure_branch_leaves_attributable_diagnostic(
    branch, n_cands, enrich_impl, synth_impl, diag_file, stage, failure_class,
    tmp_path, monkeypatch,
):
    """SC-9 覆盖面：**每个**未交付分支都留下带 stage/failure_class/date/detail 的诊断。

    含重生成耗尽路径（`synthesis_json_truncated` 走的就是 2 次重生成后仍失败）。
    """
    _Vault(tmp_path, monkeypatch)

    with (
        patch("akb2.briefing.github.pipeline._real_intake",
              return_value=(_candidates(n_cands), "layer2", {})),
        patch("akb2.briefing.github.pipeline.enrich", side_effect=enrich_impl),
        patch("akb2.briefing.github.pipeline.synthesize", side_effect=synth_impl),
    ):
        rc = run_pipeline(mock=False)

    assert rc != 0
    diag = _read_diag(tmp_path, diag_file)
    for key in ("status", "date", "stage", "failure_class", "reason", "detail", "remediable"):
        assert key in diag, f"{branch}: 诊断缺 {key}（告警需要它）: {diag}"
        assert diag[key] != "" and diag[key] is not None, f"{branch}: {key} 为空: {diag}"
    assert diag["date"] == date.today().isoformat()
    assert diag["stage"] == stage, f"{branch}: stage={diag['stage']}"
    assert diag["failure_class"] == failure_class, f"{branch}: {diag['failure_class']}"
    # detail 必须是可执行的下一步，不是"请检查日志"这类空话
    assert len(diag["detail"]) >= 10, diag["detail"]
    assert "检查日志" not in diag["detail"]


def test_vault_root_missing_leaves_config_class_diagnostic(tmp_path, monkeypatch):
    """最早的 preflight 分支也要留诊断，且标成 config（补跑无用，需人工）。"""
    monkeypatch.setenv("VAULT_ROOT", "")
    monkeypatch.setattr(gh_pipeline, "_PROJECT_ROOT", tmp_path)
    assert run_pipeline(mock=False) != 0
    diag = _read_diag(tmp_path, "briefing-skipped.json")
    assert diag["stage"] == gh_pipeline.FAILURE_STAGE_PREFLIGHT
    assert diag["failure_class"] == gh_pipeline.FAILURE_CLASS_CONFIG
    assert diag["remediable"] is False, "配置错标成可补救会让 backstop 白跑"


def test_output_budget_truncation_marked_not_remediable(tmp_path, monkeypatch):
    """finish_reason=length 必须标 remediable=False —— 同上限重跑必然截在同一处。"""
    from akb2.briefing.github.synthesis import SynthesisTruncatedError

    _Vault(tmp_path, monkeypatch)

    def synth(repos, api_key=None, *, mock=False):
        raise SynthesisTruncatedError("LLM 输出撞 max_tokens 上限被截断（finish_reason=length）")

    with (
        patch("akb2.briefing.github.pipeline._real_intake",
              return_value=(_candidates(7), "layer2", {})),
        patch("akb2.briefing.github.pipeline.enrich", side_effect=_enrich_ok),
        patch("akb2.briefing.github.pipeline.synthesize", side_effect=synth),
    ):
        assert run_pipeline(mock=False) != 0

    diag = _read_diag(tmp_path, "briefing-failed.json")
    assert diag["failure_class"] == gh_pipeline.FAILURE_CLASS_OUTPUT_BUDGET
    assert diag["remediable"] is False
    assert "MAX_OUTPUT_TOKENS" in diag["detail"]


def test_every_diagnostic_call_site_passes_stage_and_failure_class():
    """静态闸：`_write_skipped` / `_write_failed` 的每个调用点都显式传 stage+failure_class。

    两个参数有默认值（失败路径上不该因忘传而抛 TypeError 丢掉整份诊断），
    所以"必须显式传"只能靠这条静态断言守 —— 否则新增分支会静默落成 unknown/internal。
    """
    # 调用点分布在 _collect_top10（intake→score，从 run_pipeline 拆出以支持 top10 缓存）
    # 与 run_pipeline（synthesize→写盘）两处，两处都要守。
    source = inspect.getsource(gh_pipeline._collect_top10) + inspect.getsource(
        gh_pipeline.run_pipeline
    )
    calls = re.findall(r"_write_(?:skipped|failed)\((?:[^()]|\([^()]*\))*\)", source)
    assert len(calls) >= 11, f"调用点数量异常（{len(calls)}），正则可能失配"
    missing = [c for c in calls if "stage=" not in c or "failure_class=" not in c]
    assert missing == [], f"这些调用点没显式传 stage/failure_class: {missing}"


def test_regeneration_budget_is_named_and_bounded():
    """预算是具名常量且有界（宪法 P1/P4）。"""
    assert isinstance(gh_pipeline.MAX_SYNTHESIS_REGENERATIONS, int)
    assert 1 <= gh_pipeline.MAX_SYNTHESIS_REGENERATIONS <= 3, "重生成预算必须有界且不奢侈"
    assert gh_pipeline.REGEN_REASON_JSON_TRUNCATED == "json_truncated"
    assert gh_pipeline.REGEN_REASON_SCHEMA_INVALID == "schema_invalid"


# ── 降级可见性：模型标识必须落进产物（宪法 P4）────────────────────────────────

def _synth_with_model(model: str):
    """模拟族降级后的 synthesize 返回：带 synthesis_model 的正常产出。"""
    def _inner(repos, api_key=None, *, mock=False):
        out = _synth_ok(repos, api_key, mock=mock)
        out["synthesis_model"] = model
        return out
    return _inner


class TestSynthesisModelRecordedInArtifact:
    """降级必须落进产物，不能只在 stdout 一闪而过（宪法 P4「失败可见」）。

    背景：2026-08-02 预算耗尽故障后加的族降级（主族 → gemini-flash 兜底）原先只打一行
    `SYNTHESIS_FALLBACK` 到 stdout。读简报的人、以及事后审计「这份是谁产的」的人，
    从产物里看不出任何痕迹 —— 而 stdout 在 launchd 下只落日志文件、无人常看。
    走真实端到端交付路径断言，而不是手拼 briefing dict（那样测不到接线）。
    """

    FALLBACK_MODEL = "gemini-3.5-flash"

    def test_fallback_model_lands_in_markdown_frontmatter(self, tmp_path, monkeypatch):
        vault = _Vault(tmp_path, monkeypatch)
        with (
            patch("akb2.briefing.github.pipeline._real_intake",
                  return_value=(_candidates(7), "layer2", {})),
            patch("akb2.briefing.github.pipeline.enrich", side_effect=_enrich_ok),
            patch("akb2.briefing.github.pipeline.synthesize",
                  side_effect=_synth_with_model(self.FALLBACK_MODEL)),
        ):
            rc = run_pipeline(mock=False)

        assert rc == gh_pipeline.EXIT_DELIVERED
        head = vault.today_md.read_text(encoding="utf-8").split("---")[1]
        assert f"synthesis_model: {self.FALLBACK_MODEL}" in head, (
            f"frontmatter 未暴露实际模型，降级对读者不可见:\n{head}"
        )

    def test_delivery_still_valid_when_model_absent(self, tmp_path, monkeypatch):
        """未带 synthesis_model 时不得写空值 —— schema 是 minLength:1，写空会 fail schema
        并把整条链路从"能交付"变成"交付不了"。"""
        vault = _Vault(tmp_path, monkeypatch)
        with (
            patch("akb2.briefing.github.pipeline._real_intake",
                  return_value=(_candidates(7), "layer2", {})),
            patch("akb2.briefing.github.pipeline.enrich", side_effect=_enrich_ok),
            patch("akb2.briefing.github.pipeline.synthesize", side_effect=_synth_ok),
        ):
            rc = run_pipeline(mock=False)

        assert rc == gh_pipeline.EXIT_DELIVERED, "缺模型字段不该让交付失败"
        head = vault.today_md.read_text(encoding="utf-8").split("---")[1]
        assert "synthesis_model:" not in head, "不得写出空的 synthesis_model 行"


# ═══════════════════════════════════════════════════════════════════════════
# SC-R1-09 —— 重生成 × 重试的预算对账（声明值 == 真实最坏值；族闩跨轮保持）
# ═══════════════════════════════════════════════════════════════════════════
#
# 这组测试**不 patch synthesize**：族闩和重试循环都在 synthesize 内部，patch 掉它
# 就等于把要测的东西换走了（本文件其余用例 patch synthesize 是因为它们测 pipeline
# 的分支判定，不测预算算术）。改打最底层的 urlopen 接缝。


def _run_pipeline_counting_requests(tmp_path, monkeypatch, fallback_behaviour):
    """跑真 synthesize（打 urlopen），返回 (每族请求数, rc, stdout, vault)。

    Args:
        fallback_behaviour: 备族第 n 次请求（1-based）该怎么应答的回调，
            返回 None 表示抛 429 限流，返回 bytes 表示 200 响应体。
    """
    from akb2.briefing.github import synthesis as syn

    vault = _Vault(tmp_path, monkeypatch)
    monkeypatch.setenv("AKB2_STATE_ROOT", str(tmp_path / "state"))
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "gw-test-key")
    monkeypatch.delenv(syn.FAMILY_FALLBACK_ENV, raising=False)
    monkeypatch.setattr(syn, "_sleep", lambda _s: None)
    syn.reset_family_latch()

    seen: list[str] = []
    fallback_hits = {"n": 0}

    def fake_urlopen(req, timeout=None):
        model = json.loads(req.data)["model"]
        seen.append(model)
        if model == syn.PRIMARY_SYNTHESIS_MODEL:
            # 主族恒 429 限流（**不是**预算体：预算体首击即抛且不换族，测不到族闩）
            raise HTTPError(
                syn.DEEPSEEK_API_URL, 429, "HTTP 429", {},
                io.BytesIO(b'{"error": {"message": "Too many requests"}}'),
            )
        fallback_hits["n"] += 1
        body = fallback_behaviour(fallback_hits["n"])
        if body is None:
            raise HTTPError(
                syn.DEEPSEEK_API_URL, 429, "HTTP 429", {},
                io.BytesIO(b'{"error": {"message": "Too many requests"}}'),
            )
        return _FakeUrlopenResponse(body)

    monkeypatch.setattr(syn, "urlopen", fake_urlopen)

    with (
        patch("akb2.briefing.github.pipeline._real_intake",
              return_value=(_candidates(7), "layer2", {})),
        patch("akb2.briefing.github.pipeline.enrich", side_effect=_enrich_ok),
    ):
        rc = run_pipeline(mock=False)

    counts = {
        syn.PRIMARY_SYNTHESIS_MODEL: seen.count(syn.PRIMARY_SYNTHESIS_MODEL),
        syn.FALLBACK_SYNTHESIS_MODEL: seen.count(syn.FALLBACK_SYNTHESIS_MODEL),
    }
    return counts, rc, seen, vault


class _FakeUrlopenResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


_BAD_JSON_200 = b'{"choices": [{"message": {"content": "\\u62b1\\u6b49"}}]}'


def test_regeneration_preserves_family_latch_and_budget_claim(
    tmp_path, monkeypatch, capsys,
):
    """两条绑定，缺一条这条缺陷就能复发：

    ① **族闩跨轮保持** —— 主族恒 429 时，整个重生成阶段主族总请求数恒为 MAX_RETRIES，
       不随轮数放大。旧代码在 `synthesize()` 入口 `_unavailable_families.clear()`，
       于是每轮重生成都复活判死的主族，实测 3 × MAX_RETRIES。
    ② **声明预算 == 实测最坏** —— `MAX_LLM_REQUESTS_PER_RUN` 必须等于脚本化最坏路径
       上真实发生的请求数。旧注释声明「最坏 6 次 LLM 调用」，R1 实测 36。
    """
    from akb2.briefing.github import synthesis as syn

    # ── ① 族闩：备族每次都 200 但吐坏 JSON → 逐轮重生成，主族只在第一轮被打 ──
    counts, rc, seen, vault = _run_pipeline_counting_requests(
        tmp_path, monkeypatch, lambda _n: _BAD_JSON_200,
    )
    out = capsys.readouterr().out

    assert counts[syn.PRIMARY_SYNTHESIS_MODEL] == syn.MAX_RETRIES, (
        f"主族请求数随重生成轮数放大了（族闩被内层清掉）: {counts}\n序列={seen}"
    )
    # 重生成真的跑满了预算（否则上面的"不放大"可能只是因为没重生成）
    assert _regen_lines(out) == [
        ("1", str(gh_pipeline.MAX_SYNTHESIS_REGENERATIONS), "json_truncated"),
        ("2", str(gh_pipeline.MAX_SYNTHESIS_REGENERATIONS), "json_truncated"),
    ], out
    # 备族每个调用点 1 次请求 × 6 个调用点
    assert counts[syn.FALLBACK_SYNTHESIS_MODEL] == gh_pipeline.SYNTHESIS_CALL_POINTS_PER_RUN
    assert rc == gh_pipeline.EXIT_NOT_DELIVERED
    assert vault.written_files() == [], "重生成耗尽后仍必须 fail-closed 不写 vault"

    # ── ② 声明预算 == 脚本化最坏路径的实测请求数 ──────────────────────────────
    # 最坏形状：每个调用点都让**当前在用的那一族**把退避表用满后才 200
    # （主族 MAX_RETRIES 次全败 → 判死换族；此后每个调用点备族 MAX_RETRIES-1 次
    #  失败 + 第 MAX_RETRIES 次 200 吐坏 JSON）。
    worst_counts, worst_rc, worst_seen, _vault2 = _run_pipeline_counting_requests(
        tmp_path / "worst", monkeypatch,
        lambda n: _BAD_JSON_200 if n % syn.MAX_RETRIES == 0 else None,
    )
    observed_total = sum(worst_counts.values())

    assert gh_pipeline.MAX_LLM_REQUESTS_PER_RUN == observed_total, (
        f"声明预算 {gh_pipeline.MAX_LLM_REQUESTS_PER_RUN} != 实测最坏 {observed_total}"
        f"（{worst_counts}）—— 声明的预算必须等于真实可发生的最大请求数（宪法 P4）"
    )
    assert worst_counts[syn.PRIMARY_SYNTHESIS_MODEL] == syn.MAX_RETRIES
    assert worst_rc == gh_pipeline.EXIT_NOT_DELIVERED
    assert len(worst_seen) == observed_total


def test_declared_llm_budget_is_derived_not_hardcoded():
    """预算常量必须由退避表/轮数推导 —— 改任一项而不同步推导就打红。"""
    from akb2.briefing.github import synthesis as syn

    assert gh_pipeline.SYNTHESIS_CALL_POINTS_PER_RUN == (
        (gh_pipeline.MAX_SYNTHESIS_REGENERATIONS + 1) * syn.SYNTHESIZE_CALL_POINTS
    )
    assert gh_pipeline.MAX_LLM_REQUESTS_PER_RUN == (
        (gh_pipeline.SYNTHESIS_CALL_POINTS_PER_RUN + 1) * syn.MAX_RETRIES
    )
    assert syn.MAX_REQUESTS_PER_SYNTHESIZE == (
        (syn.SYNTHESIZE_CALL_POINTS + 1) * syn.MAX_RETRIES
    )
