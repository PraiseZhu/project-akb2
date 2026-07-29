"""test_audit_fidelity_fp_eval.py — `evals/audit-fidelity-fp/run.py` 离线误报率评测。

为什么这个文件存在：那份评测脚本是"fail 级检查误报率"从不可测变成可测的**唯一出数
通道**，而它最容易坏的方式不是崩，是**在没有证据时吐出一个看起来像结论的数字**
（上一轮就把"恒等回放 0 误报"当过结论）。所以本文件的主断言全部对着两件事：

1. 语料不足 / 未裁决 / 恒等回放 → 必须明说"样本不足"，`false_positive_rate` 必须
   是 `None`，摘要里不得出现任何百分比；
2. 曝光面（判据多久开火一次）与误报率（开火里有多少是错的）在报告结构上分开，
   不因为前者有数就给后者填数。

绑定生产路径：
- 判据一律走生产函数 `audit._check_translation_fidelity`（经 `run.py::run_checks`），
  不在测试里复刻严重度规则；`test_tweet_url_missing_*` 就是靠这条抓"评测口径与
  生产严重度不一致"。
- 语料的**写端**用 `translate.translate_briefing()` 生产入口产出（只 mock LLM 网关），
  再由评测的读端消费，两端 schema 对不上会直接挂（见
  `test_pairs_written_by_production_translate_are_consumable`）。本仓栽过三次
  "测试里手工构造、生产从不这么走"的坑，故 producer↔consumer 必须端到端接一次。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.briefing.test_translate import (
    FIXTURE_SYNTH,
    MOCK_TRANSLATE_RESPONSE,
    _make_mock_call_llm,
)

_EVAL_REL = "evals/audit-fidelity-fp/run.py"


@pytest.fixture(scope="module")
def fp_eval(repo_root: Path):
    """加载评测脚本模块（目录名带连字符，不是包，只能按文件路径加载）。"""
    path = repo_root / _EVAL_REL
    assert path.is_file(), f"评测脚本缺失: {path}"
    spec = importlib.util.spec_from_file_location("akb2_fp_eval", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# 语料构造辅助
# ---------------------------------------------------------------------------

def _write_corpus(corpus_dir: Path, records: list[dict], *, date: str = "2026-07-28") -> Path:
    corpus_dir.mkdir(parents=True, exist_ok=True)
    target = corpus_dir / f"{date}.jsonl"
    with target.open("a", encoding="utf-8") as fh:
        for rec in records:
            rec.setdefault("date", date)
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return target


def _clean_pair(n: int) -> dict:
    """既不含数字/URL/@handle、也不含中文指标名的干净配对（不该触发任何 fail 判据）。"""
    return {
        "field": "executive_summary",
        "en": f"Agents kept improving steadily across many teams, case {'x' * (n + 1)}.",
        "zh": f"智能体在多个团队中稳步改进，第{'一' * (n + 1)}例。",
    }


def _handle_missing_pair(n: int) -> dict:
    """英文原文有 @handle、译文丢掉 → handle_missing（fail 级）。"""
    return {
        "field": "executive_summary",
        "en": f"@alice{n} shared a new agent harness recipe with the community today.",
        "zh": f"某位作者今天分享了一份新的智能体框架配方，编号{'一' * (n + 1)}。",
    }


def _today(fp_eval) -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).date().isoformat()


def _run_main(fp_eval, argv: list[str]) -> tuple[int, dict, str]:
    """跑评测 CLI，返回 (exit_code, report, stdout)。"""
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = fp_eval.main(argv)
    report_path = argv[argv.index("--report-out") + 1]
    report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    return code, report, buf.getvalue()


# ---------------------------------------------------------------------------
# 反假结论：没有证据时必须明说"样本不足"
# ---------------------------------------------------------------------------

def test_empty_corpus_reports_insufficient_not_a_zero_rate(fp_eval, tmp_path):
    """语料为空 → 明说样本不足；不得出现 0.0 误报率或任何百分比。"""
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    code, report, out = _run_main(fp_eval, [
        "--corpus-dir", str(corpus),
        "--report-out", str(tmp_path / "fp.json"),
    ])

    assert code == 3, code  # fail-visible：想拿数字的调用方拿到非零
    assert report["verdict"] == "insufficient_sample", report["verdict"]
    assert report["corpus"]["pairs_usable"] == 0
    for kind in fp_eval.FAIL_KINDS:
        entry = report["false_positive"]["per_kind"][kind]
        assert entry["false_positive_rate"] is None, (kind, entry)
        assert entry["insufficient_reason"].startswith("insufficient_corpus"), entry
    assert "样本不足，无法给出误报率" in out
    assert "%" not in out, f"空语料的摘要里出现了百分比（假结论）:\n{out}"


def test_identity_mode_refuses_rate_even_when_fully_adjudicated(fp_eval, tmp_path):
    """恒等回放（en == zh）即使配对够、裁决全 → 误报率仍必须是 None。

    这是上一轮"恒等回放 0 误报"同义反复的回归锁：en == zh 时数字/URL/@handle 必然
    逐字保留，判据在构造上不可能报错，任何数字都不是证据。
    """
    pairs = [
        {"field": "executive_summary", "en": f"@bob{i} shipped a thing", "zh": "某人发布了东西"}
        for i in range(fp_eval.MIN_PAIRS_FOR_RATE + 5)
    ]
    triggers, check_stats = fp_eval.run_checks(pairs)
    assert triggers, "构造失败：应至少触发一次 handle_missing"
    labels = {t["id"]: {"verdict": "true_positive"} for t in triggers}

    report = fp_eval.build_report(
        corpus_mode="identity",
        corpus_dir="/vault",
        corpus={"stats": {
            "files": 1, "lines": 0, "malformed_lines": 0,
            "pairs_total": len(pairs), "pairs_expired_excluded": 0,
            "pairs_identity_excluded": 0, "pairs_usable": len(pairs),
            "dates": ["2026-07-28"], "days_total": 1,
        }},
        triggers=triggers,
        check_stats=check_stats,
        labels=labels,
        max_age_days=30,
    )

    assert report["verdict"] == "exposure_only", report["verdict"]
    for kind in fp_eval.FAIL_KINDS:
        entry = report["false_positive"]["per_kind"][kind]
        assert entry["false_positive_rate"] is None, (kind, entry)
        assert "identity_replay_cannot_measure_false_positive" in entry["insufficient_reason"]
    # 曝光面照常有数（两个概念分开：一个有数不代表另一个也有数）
    assert report["exposure"]["per_kind"]["handle_missing"]["triggers"] > 0
    summary = fp_eval.render_summary(report)
    assert "%" not in summary, summary


def test_exposure_present_but_rate_unmeasured_without_adjudication(fp_eval, tmp_path):
    """真实配对够量、判据确实开火，但没有人工裁决 → 曝光面出数、误报率仍是 None。"""
    corpus = tmp_path / "corpus"
    records = [_handle_missing_pair(i) for i in range(6)]
    records += [_clean_pair(i) for i in range(fp_eval.MIN_PAIRS_FOR_RATE)]
    _write_corpus(corpus, records, date=_today(fp_eval))

    code, report, out = _run_main(fp_eval, [
        "--corpus-dir", str(corpus),
        "--report-out", str(tmp_path / "fp.json"),
    ])

    assert code == 3, code
    assert report["verdict"] == "insufficient_sample", report["verdict"]
    exposure = report["exposure"]["per_kind"]["handle_missing"]
    assert exposure["triggers"] == 6, exposure
    assert exposure["trigger_rate_per_pair"] > 0
    fp = report["false_positive"]["per_kind"]["handle_missing"]
    assert fp["false_positive_rate"] is None, fp
    assert fp["insufficient_reason"].startswith("insufficient_adjudication"), fp
    assert fp["pending_human"] == 6, fp
    assert len(report["pending_triggers"]) == 6
    assert "样本不足，无法给出误报率" in out


def test_rate_is_measured_only_after_enough_adjudication(fp_eval, tmp_path):
    """裁决够量后才出误报率，且数值 = 假阳/裁决数，退出码回 0。"""
    corpus = tmp_path / "corpus"
    records = [_handle_missing_pair(i) for i in range(5)]
    records += [_clean_pair(i) for i in range(fp_eval.MIN_PAIRS_FOR_RATE)]
    _write_corpus(corpus, records, date=_today(fp_eval))

    # 先无裁决跑一次，从 pending_triggers 拿稳定 id
    _code, report, _out = _run_main(fp_eval, [
        "--corpus-dir", str(corpus),
        "--report-out", str(tmp_path / "pass1.json"),
    ])
    ids = [t["id"] for t in report["pending_triggers"]]
    assert len(ids) == fp_eval.MIN_ADJUDICATED_PER_KIND == 5, ids

    labels = {ids[0]: {"verdict": "false_positive"}}
    labels.update({i: {"verdict": "true_positive"} for i in ids[1:]})
    labels_path = tmp_path / "labels.json"
    labels_path.write_text(json.dumps(labels), encoding="utf-8")

    code, report2, out = _run_main(fp_eval, [
        "--corpus-dir", str(corpus),
        "--labels", str(labels_path),
        "--report-out", str(tmp_path / "pass2.json"),
    ])

    assert code == 0, out
    assert report2["verdict"] == "measured", report2["verdict"]
    fp = report2["false_positive"]["per_kind"]["handle_missing"]
    assert fp["adjudicated_total"] == 5
    assert fp["false_positive"] == 1
    assert fp["true_positive"] == 4
    assert fp["false_positive_rate"] == pytest.approx(0.2)
    assert fp["confidence"] == "量级估计"  # 5 条只够量级，报告必须如实标注
    assert fp["insufficient_reason"] is None
    assert "20.0%" in out


# ---------------------------------------------------------------------------
# 判据口径必须来自生产
# ---------------------------------------------------------------------------

def test_real_number_defect_triggers_production_fail_kind(fp_eval, tmp_path):
    """英文 405B 被译成"80 亿参数" → 生产判据报 number_mismatch，评测必须收得到。"""
    corpus = tmp_path / "corpus"
    _write_corpus(corpus, [{
        "field": "executive_summary",
        "en": "Meta released Llama 405B this morning to selected partners.",
        "zh": "Meta 今早向部分合作伙伴发布了 80 亿参数的 Llama 模型。",
    }], date=_today(fp_eval))

    _code, report, _out = _run_main(fp_eval, [
        "--corpus-dir", str(corpus),
        "--report-out", str(tmp_path / "fp.json"),
    ])

    assert report["exposure"]["per_kind"]["number_mismatch"]["triggers"] == 1, report
    assert report["exposure"]["pairs_checked"] == 1
    assert report["exposure"]["pairs_unrecognized_field"] == 0
    assert "405B" in report["pending_triggers"][0]["msg"]


def test_tweet_url_missing_stays_warn_and_is_not_counted(fp_eval, tmp_path):
    """严重度取自生产：推文正文丢 URL 只是 warn，不得被评测算成 fail 级触发。

    生产里推文正文 URL 会被 `pipeline._strip_inline_links` 剥离，故只判 warn；分析
    字段的 URL 会逐字进产出，判 fail。评测若把两者一律当 fail，误报率口径就与生产
    脱钩（本仓栽过"测试里手工传参、生产从不这么走"的同类坑）。
    """
    corpus = tmp_path / "corpus"
    _write_corpus(corpus, [
        {
            "field": "executive_summary",
            "en": "Details live at https://example.com/agents for the curious reader.",
            "zh": "细节请见官方页面，欢迎读者查阅。",
        },
        {
            "field": "tweets[3].text",
            "en": "Full writeup at https://example.com/thread with benchmarks.",
            "zh": "完整说明见帖内链接，含基准结果。",
        },
    ], date=_today(fp_eval))

    _code, report, _out = _run_main(fp_eval, [
        "--corpus-dir", str(corpus),
        "--report-out", str(tmp_path / "fp.json"),
    ])

    assert report["exposure"]["pairs_checked"] == 2
    exposure = report["exposure"]["per_kind"]["url_missing"]
    assert exposure["triggers"] == 1, report["pending_triggers"]
    assert report["pending_triggers"][0]["field"] == "executive_summary"


def test_token_family_exposure_is_reported(fp_eval, tmp_path):
    """报告须给出各正则家族在**受检侧（en）**的 token 曝光面。

    zh 侧故意把这些 token 全本地化掉：曝光面若误算在 zh 侧会得 0，从而锁住"扫的是
    判据真正读的那一侧"。audit 注释里的 token 曝光数字靠本模式复现。
    """
    corpus = tmp_path / "corpus"
    _write_corpus(corpus, [{
        "field": "executive_summary",
        "en": "Llama 405B and v1.2.3 shipped to 10,000 users, per @alice.",
        "zh": "四千零五十亿参数的 Llama 与该版本已向一万名用户发布，消息来自某位作者。",
    }], date=_today(fp_eval))

    _code, report, out = _run_main(fp_eval, [
        "--corpus-dir", str(corpus),
        "--report-out", str(tmp_path / "fp.json"),
    ])

    families = report["exposure"]["token_families"]
    assert families["param_scale"]["tokens"] == 1, families
    assert families["version"]["tokens"] == 1, families
    assert families["thousands"]["tokens"] == 1, families
    assert families["handle"]["tokens"] == 1, families
    assert families["param_scale"]["pairs"] == 1
    assert families["param_scale"]["days"] == 1
    assert "token 曝光面" in out


# ---------------------------------------------------------------------------
# 语料读端：损耗必须可见
# ---------------------------------------------------------------------------

def test_malformed_expired_and_identity_lines_are_counted_not_dropped(fp_eval, tmp_path, capsys):
    """坏行/过期/恒等三类损耗都要进统计，否则"语料变少"会被读成"判据不开火了"。"""
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    good = _handle_missing_pair(0) | {"date": _today(fp_eval)}
    identity = {"field": "executive_summary", "en": "same text", "zh": "same text",
                "date": _today(fp_eval)}
    expired = _handle_missing_pair(1) | {"date": "2000-01-01"}
    lines = [json.dumps(good, ensure_ascii=False),
             "{not json",
             json.dumps(identity, ensure_ascii=False),
             json.dumps(expired, ensure_ascii=False)]
    (corpus / f"{_today(fp_eval)}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    _code, report, _out = _run_main(fp_eval, [
        "--corpus-dir", str(corpus),
        "--report-out", str(tmp_path / "fp.json"),
    ])

    c = report["corpus"]
    assert c["lines"] == 4, c
    assert c["malformed_lines"] == 1, c
    assert c["pairs_total"] == 3, c
    assert c["pairs_identity_excluded"] == 1, c
    assert c["pairs_expired_excluded"] == 1, c
    assert c["pairs_usable"] == 1, c
    assert "坏行" in capsys.readouterr().err


def test_unknown_field_is_reported_not_silently_skipped(fp_eval, tmp_path, capsys):
    """未识别字段名必须计数 + 告警，不能静默不判（会把漏判读成"没问题"）。"""
    corpus = tmp_path / "corpus"
    _write_corpus(corpus, [{
        "field": "key_quotes[0].quote",
        "en": "@alice said something", "zh": "某人说了些话",
    }], date=_today(fp_eval))

    _code, report, _out = _run_main(fp_eval, [
        "--corpus-dir", str(corpus),
        "--report-out", str(tmp_path / "fp.json"),
    ])

    assert report["exposure"]["pairs_unrecognized_field"] == 1, report["exposure"]
    assert report["exposure"]["pairs_checked"] == 0
    assert "未识别字段" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# producer ↔ consumer 端到端：写端是生产入口
# ---------------------------------------------------------------------------

def test_pairs_written_by_production_translate_are_consumable(fp_eval, tmp_path):
    """`translate_briefing()` 落盘的语料必须能被评测原样消费（两端 schema 同源）。

    唯一 mock 点 = LLM 网关 `synthesis._call_llm`；采样目录由 conftest 的
    AKB2_STATE_ROOT 隔离，不碰生产状态。
    """
    from akb2.briefing import translate as T

    with patch(
        "akb2.briefing.synthesis._call_llm",
        side_effect=_make_mock_call_llm(MOCK_TRANSLATE_RESPONSE),
    ):
        T.translate_briefing(FIXTURE_SYNTH)

    sample_dir = T.fidelity_sample_dir()
    written = sum(
        1
        for path in sample_dir.glob("*.jsonl")
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    assert written > 0, "生产入口没有落盘任何配对，评测无语料可读"

    _code, report, _out = _run_main(fp_eval, [
        "--corpus-dir", str(sample_dir),
        # FIXTURE_SYNTH 的日期是固定过去日，按保留天数会被判过期；这里放宽年龄门，
        # 被测的是 schema 兼容而非保留策略（保留策略另有 expired 用例覆盖）。
        "--max-age-days", "100000",
        "--report-out", str(tmp_path / "fp.json"),
    ])

    c = report["corpus"]
    assert c["malformed_lines"] == 0, c
    assert c["pairs_total"] == written, (c, written)
    assert c["pairs_usable"] == written, c
    assert report["exposure"]["pairs_checked"] == written, report["exposure"]
    assert report["exposure"]["pairs_unrecognized_field"] == 0, report["exposure"]


def test_default_corpus_dir_is_translate_sample_dir(fp_eval, tmp_path):
    """不传 --corpus-dir 时读的就是 translate 的采样目录（免"两边各指一处"）。"""
    from akb2.briefing import translate as T

    _code, report, _out = _run_main(fp_eval, [
        "--report-out", str(tmp_path / "fp.json"),
    ])
    assert report["corpus"]["dir"] == str(T.fidelity_sample_dir())


def test_identity_mode_requires_vault_root(fp_eval, tmp_path):
    code = fp_eval.main(["--corpus-mode", "identity"])
    assert code == 2, code
