"""视频链路凭证类故障回归（2026-07-30 事故）。

事故实况（`logs/video-blocked.json` + `logs/video-stderr.log`）：
漏斗 源 21 → 列表 246 → 候选 2 → 发布 0，连续 2 轮 blocked。三处症状：

1. 日期补取 11 次里 9 次撞 YouTube 机器人校验（需 cookies）→ 9 候选被时间闸拒收；
2. NLM auth 过期（`profile 'default' 不可用`）→ 研究降级；
3. 降级路径的 yt-dlp 字幕获取撞同一堵墙 → 研究 0/2。

共同根因只有一个（外部凭证失效），但落盘 `reason` 只报了最末一层的
`date_unresolvable`，且 researcher 的降级路径根本不带 cookies。

两组断言都绑**可观测行为**，不是"能跑通"：
- SC1：真实传给 `subprocess` 的 argv（两个 yt-dlp 调用点各测配置态 / 未配置态）；
- SC2：落盘 JSON 的 `reason` / `credential_health` / `fix_commands` 字段。
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from src.akb2.video.blocked import (
    STATUS_BLOCKED,
    STATUS_ZERO_YIELD,
    VIDEO_BLOCKED_REL_PATH,
    classify,
    write_zero_yield_diagnostic,
)
from src.akb2.video.collector import CollectStats, VideoCollector
from src.akb2.video.config import VideoConfig
from src.akb2.video.external_credentials import (
    CREDENTIAL_NLM_AUTH,
    CREDENTIAL_YTDLP_COOKIES,
    ENV_COOKIES_FILE,
    ENV_COOKIES_FROM_BROWSER,
    SOURCE_BROWSER_FALLBACK,
    SOURCE_CONFIG_FILE,
    SOURCE_ENV_BROWSER,
    SOURCE_ENV_FILE,
    SOURCE_ENV_FILE_MISSING,
    STATUS_DEAD,
    STATUS_DEGRADED,
    STATUS_OK,
    CredentialHealth,
    CredentialItem,
    cookie_args,
    probe_credentials,
    probe_nlm_auth,
    probe_ytdlp_cookies,
    resolve_cookie_config,
)
from src.akb2.video.researcher import VideoResearcher
from tests.video_state_isolation import isolate_video_state  # noqa: F401

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_cookie_env(monkeypatch):
    """每个用例从"env 未配置"起步，避免本机真实 env 泄进断言。"""
    monkeypatch.delenv(ENV_COOKIES_FILE, raising=False)
    monkeypatch.delenv(ENV_COOKIES_FROM_BROWSER, raising=False)


@pytest.fixture
def cookie_file(tmp_path: Path) -> Path:
    path = tmp_path / "yt-cookies.txt"
    path.write_text("# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tx\n")
    return path


def _collector(tmp_path: Path) -> VideoCollector:
    """最小 collector：无 video_sources.json → settings 走默认（浏览器兜底）。"""
    config = VideoConfig(
        video_sources_path=tmp_path / "video_sources.json",
        akb2_root=tmp_path / "akb2",
        staging_dir=tmp_path / "staging",
        dedup_db_path=tmp_path / "dedup.sqlite",
    )
    return VideoCollector(config)


def _researcher(tmp_path: Path) -> VideoResearcher:
    config = VideoConfig(
        video_sources_path=tmp_path / "video_sources.json",
        akb2_root=tmp_path / "akb2",
        staging_dir=tmp_path / "staging",
        dedup_db_path=tmp_path / "dedup.sqlite",
        ytdlp_bin="/usr/bin/false-ytdlp",
    )
    return VideoResearcher(config)


def _funnel(**overrides) -> dict:
    """事故当天的真实漏斗形态（可覆盖单项）。"""
    stats = CollectStats()
    stats.sources_enabled = 21
    stats.raw_listed = 246
    stats.date_backfill_used = 11
    stats.date_backfill_ok = 2
    stats.date_backfill_failed = 9
    stats.rejected_date_unknown = 9
    stats.candidates_after_filters = 2
    payload = stats.as_dict()
    payload.update(overrides)
    return payload


def _health(cookies_status: str = STATUS_OK, nlm_status: str = STATUS_OK) -> CredentialHealth:
    return CredentialHealth(
        items=(
            CredentialItem(
                name=CREDENTIAL_YTDLP_COOKIES,
                status=cookies_status,
                detail="无显式 cookie 文件，回退 --cookies-from-browser",
                fix_command="" if cookies_status == STATUS_OK else "导出 cookies 后 export AKB2_YTDLP_COOKIES=...",
                source=SOURCE_BROWSER_FALLBACK,
            ),
            CredentialItem(
                name=CREDENTIAL_NLM_AUTH,
                status=nlm_status,
                detail="NLM auth 过期 — profile 'default' 不可用",
                fix_command="" if nlm_status == STATUS_OK else "终端运行: nlm login --profile default",
            ),
        )
    )


# ===========================================================================
# SC1 · cookies 配置统一（两个 yt-dlp 调用点）
# ===========================================================================


def test_env_cookie_file_resolves_to_cookies_flag(cookie_file):
    cfg = resolve_cookie_config({ENV_COOKIES_FILE: str(cookie_file)})
    assert cfg.args == ("--cookies", str(cookie_file))
    assert cfg.source == SOURCE_ENV_FILE
    assert cfg.configured is True


def test_env_browser_resolves_to_cookies_from_browser_flag():
    cfg = resolve_cookie_config({ENV_COOKIES_FROM_BROWSER: "chrome"})
    assert cfg.args == ("--cookies-from-browser", "chrome")
    assert cfg.source == SOURCE_ENV_BROWSER


def test_env_file_wins_over_browser_when_both_set(cookie_file):
    """二选一：同时配置按文件优先，且不静默（冲突要能在日志里看到）。"""
    cfg = resolve_cookie_config({
        ENV_COOKIES_FILE: str(cookie_file),
        ENV_COOKIES_FROM_BROWSER: "chrome",
    })
    assert cfg.args == ("--cookies", str(cookie_file))


def test_missing_env_cookie_file_is_not_injected(tmp_path):
    """配了但文件不存在 → 不注入坏路径（否则 yt-dlp 整体报错，连兜底一起打死）。"""
    cfg = resolve_cookie_config({ENV_COOKIES_FILE: str(tmp_path / "nope.txt")})
    assert cfg.args == ()
    assert cfg.source == SOURCE_ENV_FILE_MISSING
    assert cfg.configured is False


def test_empty_env_cookie_file_is_not_injected(tmp_path):
    empty = tmp_path / "empty.txt"
    empty.touch()
    cfg = resolve_cookie_config({ENV_COOKIES_FILE: str(empty)})
    assert cfg.source == SOURCE_ENV_FILE_MISSING


def test_unconfigured_env_yields_no_cookie_args():
    assert cookie_args({}) == []
    assert resolve_cookie_config({}).configured is False


# -- 调用点 1：collector（列表 + 日期补取共用 _ytdlp_base_args）---------------


def test_collector_argv_uses_env_cookie_file(tmp_path, cookie_file, monkeypatch):
    monkeypatch.setenv(ENV_COOKIES_FILE, str(cookie_file))
    collector = _collector(tmp_path)

    with mock.patch("src.akb2.video.collector.subprocess.run") as run:
        run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
        collector._fill_upload_date({"url": "https://www.youtube.com/watch?v=x"})

    argv = run.call_args[0][0]
    assert "--cookies" in argv
    assert str(cookie_file) in argv
    # env 显式配置必须替掉浏览器兜底，否则等于两套 cookies 打架
    assert "--cookies-from-browser" not in argv
    assert collector.cookie_source == SOURCE_ENV_FILE


def test_collector_argv_uses_env_browser(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_COOKIES_FROM_BROWSER, "firefox")
    collector = _collector(tmp_path)

    with mock.patch("src.akb2.video.collector.subprocess.run") as run:
        run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
        collector._fill_upload_date({"url": "https://www.youtube.com/watch?v=x"})

    argv = run.call_args[0][0]
    assert argv[argv.index("--cookies-from-browser") + 1] == "firefox"
    assert collector.cookie_source == SOURCE_ENV_BROWSER


def test_collector_argv_unchanged_without_env(tmp_path):
    """未配置 env → 命令行与改动前逐字一致（浏览器兜底 + remote-components）。"""
    collector = _collector(tmp_path)
    assert collector._ytdlp_base_args == [
        "--cookies-from-browser", "chrome",
        "--remote-components", "ejs:github",
    ]
    assert collector.cookie_source == SOURCE_BROWSER_FALLBACK


def test_collector_env_does_not_drop_remote_components(tmp_path, cookie_file, monkeypatch):
    """`--remote-components` 与 cookies 无关（反爬组件），env 接管后不得被顺手删掉。"""
    monkeypatch.setenv(ENV_COOKIES_FILE, str(cookie_file))
    collector = _collector(tmp_path)
    assert collector._ytdlp_base_args[-2:] == ["--remote-components", "ejs:github"]


def test_collector_records_config_file_source(tmp_path):
    """`video_sources.json` 指定的 cookie 文件存在 → 来源记为 config_file（健康）。"""
    (tmp_path / "yt-cookies.txt").write_text("# cookies\n")
    (tmp_path / "video_sources.json").write_text(json.dumps({"creators": [], "settings": {}}))
    collector = _collector(tmp_path)
    assert collector.cookie_source == SOURCE_CONFIG_FILE
    assert "--cookies" in collector._ytdlp_base_args


# -- 调用点 2：researcher 的字幕降级路径 -------------------------------------


def test_researcher_transcript_argv_uses_env_cookie_file(tmp_path, cookie_file, monkeypatch):
    """事故 3：降级路径过去完全不带 cookies，撞与日期补取同一堵墙。"""
    monkeypatch.setenv(ENV_COOKIES_FILE, str(cookie_file))
    researcher = _researcher(tmp_path)

    with mock.patch("src.akb2.video.researcher.subprocess.run") as run:
        run.return_value = mock.Mock(returncode=1, stdout="", stderr="bot check")
        researcher._get_transcript("https://www.youtube.com/watch?v=x", "youtube")

    argv = run.call_args[0][0]
    assert "--cookies" in argv
    assert str(cookie_file) in argv
    # cookies 必须在动作参数之前（yt-dlp 的全局选项位置），且不破坏原有参数
    assert argv.index("--cookies") < argv.index("--skip-download")


def test_researcher_transcript_argv_uses_env_browser(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_COOKIES_FROM_BROWSER, "chrome")
    researcher = _researcher(tmp_path)

    with mock.patch("src.akb2.video.researcher.subprocess.run") as run:
        run.return_value = mock.Mock(returncode=1, stdout="", stderr="")
        researcher._get_transcript("https://www.youtube.com/watch?v=x", "youtube")

    argv = run.call_args[0][0]
    assert argv[argv.index("--cookies-from-browser") + 1] == "chrome"


def test_researcher_transcript_argv_unchanged_without_env(tmp_path):
    """未配置 env → 字幕命令行与改动前逐字一致（不带任何 cookies 参数）。"""
    researcher = _researcher(tmp_path)

    with mock.patch("src.akb2.video.researcher.subprocess.run") as run:
        run.return_value = mock.Mock(returncode=1, stdout="", stderr="")
        researcher._get_transcript("https://www.youtube.com/watch?v=x", "youtube")

    argv = run.call_args[0][0]
    assert "--cookies" not in argv
    assert "--cookies-from-browser" not in argv
    assert argv[0] == "/usr/bin/false-ytdlp"
    assert argv[1] == "--skip-download"


# ===========================================================================
# SC2 · 凭证预检 + credential_health 归因
# ===========================================================================


def _ok_runner(*args, **kwargs):
    return mock.Mock(returncode=0, stdout="2026.06.09", stderr="")


def _broken_runner(*args, **kwargs):
    raise FileNotFoundError("no such file: yt-dlp")


def test_probe_ytdlp_binary_missing_is_dead():
    item = probe_ytdlp_cookies("/nope/yt-dlp", SOURCE_ENV_FILE, runner=_broken_runner)
    assert item.status == STATUS_DEAD
    assert item.fix_command


def test_probe_ytdlp_browser_fallback_is_degraded_not_dead():
    """兜底浏览器 cookies **可能**能用 —— 不许单凭静态配置判死。"""
    item = probe_ytdlp_cookies("yt-dlp", SOURCE_BROWSER_FALLBACK, runner=_ok_runner)
    assert item.status == STATUS_DEGRADED
    assert ENV_COOKIES_FILE in item.fix_command


def test_probe_ytdlp_env_file_is_ok_without_fix_noise():
    item = probe_ytdlp_cookies("yt-dlp", SOURCE_ENV_FILE, runner=_ok_runner)
    assert item.status == STATUS_OK
    assert item.fix_command == ""


def test_probe_nlm_auth_expired_is_dead_with_login_command():
    item = probe_nlm_auth(lambda: "auth_expired", "default")
    assert item.status == STATUS_DEAD
    assert "nlm login --profile default" in item.fix_command


def test_probe_nlm_network_error_is_degraded_not_dead():
    """网络 transient ≠ 登录态失效（researcher 会转 pending 重试）。"""
    assert probe_nlm_auth(lambda: "network_error", "default").status == STATUS_DEGRADED


def test_probe_nlm_probe_exception_does_not_fabricate_death():
    def boom() -> str:
        raise RuntimeError("nlm 崩了")

    assert probe_nlm_auth(boom, "default").status == STATUS_DEGRADED


def test_probe_credentials_overall_is_worst_item():
    health = probe_credentials(
        ytdlp_bin="yt-dlp",
        cookie_source=SOURCE_ENV_FILE,
        nlm_profile="default",
        nlm_health_check=lambda: "auth_expired",
        runner=_ok_runner,
    )
    assert health.overall == STATUS_DEAD
    assert health.is_healthy is False
    assert health.status_of(CREDENTIAL_YTDLP_COOKIES) == STATUS_OK
    assert health.fix_commands  # 修复命令必须能直接播报


def test_probe_credentials_all_ok_is_healthy():
    health = probe_credentials(
        ytdlp_bin="yt-dlp",
        cookie_source=SOURCE_ENV_FILE,
        nlm_profile="default",
        nlm_health_check=lambda: "ok",
        runner=_ok_runner,
    )
    assert health.is_healthy is True
    assert health.fix_commands == []


# -- 归因：凭证根因优先于 date_unresolvable 等症状 ---------------------------


def test_dead_nlm_auth_with_candidates_reports_credential_root_cause():
    """事故 2：NLM 登录态死 + 有候选进研究阶段 → 报凭证根因，不是 date_unresolvable。"""
    verdict = classify(
        _funnel(), published=0,
        credential_health=_health(nlm_status=STATUS_DEAD),
    )
    assert verdict.status == STATUS_BLOCKED
    assert verdict.reason == "credential_expired"
    assert "nlm login" in verdict.detail


def test_degraded_cookies_with_backfill_failures_reports_suspect_not_date_unresolvable():
    """事故 1：只有浏览器兜底 cookies + 补取成批失败 → 疑似凭证，明示修复命令。"""
    verdict = classify(
        _funnel(), published=0,
        credential_health=_health(cookies_status=STATUS_DEGRADED),
    )
    assert verdict.reason == "credential_suspect"
    assert "AKB2_YTDLP_COOKIES" in verdict.detail
    assert "9" in verdict.detail  # 症状计数要带上，否则等于只报"可能是 cookies"


def test_dead_credential_outranks_suspect_when_both_present():
    verdict = classify(
        _funnel(), published=0,
        credential_health=_health(cookies_status=STATUS_DEGRADED, nlm_status=STATUS_DEAD),
    )
    assert verdict.reason == "credential_expired"
    assert "yt-dlp cookies" in verdict.detail and "NLM 登录态" in verdict.detail


def test_healthy_credentials_keep_original_date_unresolvable_reason():
    """凭证健康 → 归因退回原有真因，不许因为多传了个参数就改口径。"""
    verdict = classify(_funnel(), published=0, credential_health=_health())
    assert verdict.reason == "date_unresolvable"


def test_no_credential_probe_keeps_original_reason():
    assert classify(_funnel(), published=0).reason == "date_unresolvable"
    assert classify(_funnel(), published=0, credential_health=None).reason == "date_unresolvable"


def test_dead_nlm_without_candidates_is_not_blamed():
    """没有候选进研究阶段 → NLM 死活与本轮 0 产出无关，不许张冠李戴。"""
    verdict = classify(
        _funnel(candidates_after_filters=0, date_backfill_failed=0,
                date_backfill_used=0, rejected_date_unknown=0,
                dropped_relevance=246),
        published=0,
        credential_health=_health(nlm_status=STATUS_DEAD),
    )
    assert verdict.reason != "credential_expired"
    assert verdict.status == STATUS_ZERO_YIELD


def test_degraded_cookies_without_symptom_is_not_blamed():
    """本机默认就是浏览器兜底 —— 无症状时不能把正常的空窗日报成凭证故障。"""
    verdict = classify(
        _funnel(date_backfill_used=0, date_backfill_ok=0, date_backfill_failed=0,
                rejected_date_unknown=0, dropped_relevance=246),
        published=0,
        credential_health=_health(cookies_status=STATUS_DEGRADED),
    )
    assert verdict.status == STATUS_ZERO_YIELD
    assert verdict.reason == "no_candidates_in_window"


def test_dead_cookies_with_empty_list_stage_reports_credential():
    verdict = classify(
        _funnel(raw_listed=0, date_backfill_used=0, date_backfill_failed=0,
                rejected_date_unknown=0, candidates_after_filters=0),
        published=0,
        credential_health=_health(cookies_status=STATUS_DEAD),
    )
    assert verdict.reason == "credential_expired"


# -- 落盘：credential_health 块 ---------------------------------------------


def test_diagnostic_records_credential_health_with_fix_commands(tmp_path):
    write_zero_yield_diagnostic(
        _funnel(), published=0, repo_root=tmp_path,
        credential_health=_health(cookies_status=STATUS_DEGRADED, nlm_status=STATUS_DEAD),
    )
    data = json.loads((tmp_path / VIDEO_BLOCKED_REL_PATH).read_text(encoding="utf-8"))

    assert data["reason"] == "credential_expired"
    block = data["credential_health"]
    assert block["overall"] == STATUS_DEAD
    assert set(block["items"]) == {CREDENTIAL_YTDLP_COOKIES, CREDENTIAL_NLM_AUTH}
    assert block["items"][CREDENTIAL_NLM_AUTH]["status"] == STATUS_DEAD
    assert block["items"][CREDENTIAL_NLM_AUTH]["fix_command"]
    assert any("nlm login" in cmd for cmd in block["fix_commands"])
    assert any("AKB2_YTDLP_COOKIES" in cmd for cmd in block["fix_commands"])


def test_diagnostic_omits_credential_block_when_healthy(tmp_path):
    """凭证健康 → 不写该块（每轮贴一遍"一切正常"是纯噪音）。"""
    write_zero_yield_diagnostic(
        _funnel(), published=0, repo_root=tmp_path, credential_health=_health(),
    )
    data = json.loads((tmp_path / VIDEO_BLOCKED_REL_PATH).read_text(encoding="utf-8"))
    assert "credential_health" not in data
    assert data["reason"] == "date_unresolvable"


def test_diagnostic_omits_credential_block_when_not_probed(tmp_path):
    write_zero_yield_diagnostic(_funnel(), published=0, repo_root=tmp_path)
    data = json.loads((tmp_path / VIDEO_BLOCKED_REL_PATH).read_text(encoding="utf-8"))
    assert "credential_health" not in data


def test_degraded_credential_with_output_is_not_recorded(tmp_path):
    """有产出说明兜底 cookies 还能用 —— degraded 此时是噪音，不写。"""
    write_zero_yield_diagnostic(
        _funnel(), published=3, repo_root=tmp_path,
        credential_health=_health(cookies_status=STATUS_DEGRADED),
    )
    data = json.loads((tmp_path / VIDEO_BLOCKED_REL_PATH).read_text(encoding="utf-8"))
    assert "credential_health" not in data


def test_degraded_credential_not_recorded_on_genuine_empty_window(tmp_path):
    """真·空窗日不该挂凭证告警 —— 浏览器兜底是本机默认形态，写了就是引人查假故障。"""
    write_zero_yield_diagnostic(
        _funnel(date_backfill_used=0, date_backfill_ok=0, date_backfill_failed=0,
                rejected_date_unknown=0, dropped_relevance=246),
        published=0, repo_root=tmp_path,
        credential_health=_health(cookies_status=STATUS_DEGRADED),
    )
    data = json.loads((tmp_path / VIDEO_BLOCKED_REL_PATH).read_text(encoding="utf-8"))
    assert data["reason"] == "no_candidates_in_window"
    assert "credential_health" not in data


def test_degraded_credential_not_recorded_when_budget_exhausted(tmp_path):
    """预算撞顶不是 cookies 的错 —— 归因没落到凭证上就不挂凭证块。"""
    write_zero_yield_diagnostic(
        _funnel(date_backfill_failed=0, date_backfill_ok=0, date_backfill_used=40,
                date_backfill_budget_exhausted=True),
        published=0, repo_root=tmp_path,
        credential_health=_health(cookies_status=STATUS_DEGRADED),
    )
    data = json.loads((tmp_path / VIDEO_BLOCKED_REL_PATH).read_text(encoding="utf-8"))
    assert data["reason"] == "date_backfill_budget_exhausted"
    assert "credential_health" not in data


def test_credential_root_cause_keeps_creator_errors_evidence(tmp_path):
    """凭证分支抢在 list_stage_empty 之前返回，不得吞掉后者原有的 creator 异常线索。"""
    verdict = classify(
        _funnel(raw_listed=0, date_backfill_used=0, date_backfill_failed=0,
                rejected_date_unknown=0, candidates_after_filters=0,
                creator_errors=["yt-test: HTTP 429", "bili-test: timeout"]),
        published=0,
        credential_health=_health(cookies_status=STATUS_DEAD),
    )
    assert verdict.reason == "credential_expired"
    assert "HTTP 429" in verdict.detail


def test_dead_credential_with_output_is_still_recorded(tmp_path):
    """探测判死是硬故障：即便本轮还有产出也要告警（提前发现才有意义）。"""
    write_zero_yield_diagnostic(
        _funnel(), published=3, repo_root=tmp_path,
        credential_health=_health(nlm_status=STATUS_DEAD),
    )
    data = json.loads((tmp_path / VIDEO_BLOCKED_REL_PATH).read_text(encoding="utf-8"))
    assert data["credential_health"]["overall"] == STATUS_DEAD
    assert data["status"] == "ok"  # 有产出仍是 ok，凭证块只是附加告警


def test_non_string_probe_values_do_not_kill_funnel_diagnostic(tmp_path):
    """附加块不得连坐主体：探测值不是 str 时漏斗诊断照常落盘。

    诊断文件是 0 产出归因的唯一出口 —— 它写不出来就等于故障又静默了一轮。
    """
    weird = CredentialHealth(
        items=(
            CredentialItem(
                name=CREDENTIAL_NLM_AUTH,
                status=STATUS_DEAD,
                detail=mock.Mock(),          # 非 str，直接 json.dumps 会炸
                fix_command=mock.Mock(),
            ),
        )
    )
    write_zero_yield_diagnostic(
        _funnel(), published=0, repo_root=tmp_path, credential_health=weird,
    )
    data = json.loads((tmp_path / VIDEO_BLOCKED_REL_PATH).read_text(encoding="utf-8"))
    assert data["funnel"]["rejected_date_unknown"] == 9   # 主体完整
    assert data["reason"] == "credential_expired"


def test_feishu_reader_surfaces_credential_reason(tmp_path):
    """播报侧（feishu_sync.blocked）能直接拿到凭证真因，不必改读取端。"""
    from datetime import datetime

    from src.akb2.feishu_sync.blocked import blocking_reason

    write_zero_yield_diagnostic(
        _funnel(), published=0, repo_root=tmp_path,
        credential_health=_health(nlm_status=STATUS_DEAD),
    )
    text = blocking_reason("video", datetime.now().strftime("%Y-%m-%d"), repo_root=tmp_path)
    assert text is not None
    assert "credential_expired" in text
    assert "nlm login" in text
