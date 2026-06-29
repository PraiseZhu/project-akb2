verdict: FAILED
findings:
  - severity: P1
    file: src/akb2/quality/calibration.py
    line: 368
    issue: calibration harness 只拼 9 字段 frontmatter 后直接把 `extract_result.content` 送入 runner，没有镜像生产 `raw_archiver` 的锚点注入契约，导致端到端 factory 校准在 distill self-gate 大面积失败，必跑 SC-1 verify 非 0。
    evidence: "`_compose_raw_content()` 在 src/akb2/quality/calibration.py:368-412 仅按 `FRONTMATTER_FIELDS` 生成 frontmatter 并返回原正文；生产 `archive_raw()` 在 src/akb2/quality/intake/raw_archiver.py:360-384 会注入锚点目录和正文锚点。实跑 `PYTHONPATH=src python3 -m akb2.quality.calibration --corpus evals/main-pipeline-quality/corpus.yaml --report-out reports/calibration/factory.json && ...` 退出码 1，报告 `reports/calibration/factory.json:6-14` 显示 `positive_pass_rate=0.0`、`positive_live_pass_rate=0.0`、`factory_acceptance=false`；逐条证据如 `reports/calibration/factory.json:53-58`、`:121-126`、`:257-262` 报 `compose_self_gate_failed: ... 引用的锚点 §A 在 raw 文件中不存在`。这不是公网或 key 问题，15/15 live 正样本已越过 intake_raw，失败发生在 harness 生成的 raw 形态不满足下游锚点契约。"
  - severity: P2
    file: src/akb2/quality/intake/extract.py
    line: 433
    issue: Scrapling fallback 没有实现“adaptive 失败后 fallback browser”；当前只根据 `is_anti_bot` 二选一调用一次 `_extract_via_scrapling`，普通页面 defuddle/adaptive 失败后直接落全失败签名。
    evidence: "代码在 src/akb2/quality/intake/extract.py:433-435 设置 `use_browser = is_anti_bot` 后只调用一次 `_extract_via_scrapling(..., browser_mode=use_browser)`。最小复现实跑：monkeypatch defuddle 返回 `{}`、scrapling 返回 `{}` 后调用 `extract_content()`，输出 `{'scrapling_calls': [False], 'method': 'failed', 'error': 'All extraction methods failed'}`，证明 adaptive 失败未再尝试 `browser_mode=True`。现有 `tests/test_extract_scrapling_fallback.py` 只分别测试 adaptive/browser 单分支和抛错 fail-closed，没有覆盖 adaptive->browser 串联。"
  - severity: P2
    file: src/akb2/quality/calibration.py
    line: 440
    issue: 三分类在没有传 `--preflight-report` 的默认校准路径下会把抽取器全失败误判为 `dead_link`，削弱 `dead_link / metadata_failure / extraction_failure` 诊断可信度。
    evidence: "CLI 默认 `--preflight-report` 为空（src/akb2/quality/calibration.py:870-890），`run_calibration()` 因此加载空 `preflight_lookup`（:683-685）；`_classify_failure_class()` 在 :440-456 对 `http_status is None` 且 error 含 `All extraction methods failed` 直接返回 `dead_link`。最小复现实跑 `ExtractResult(content='', word_count=0, error='All extraction methods failed')` + `http_status=None` 输出 `dead_link`，但这类结果没有 HTTP 死链证据，应保守归为 `extraction_failure` 或要求 factory 命令显式传入刚生成的 preflight 报告。"
summary: "单测、preflight 和 ruff 通过，边界 grep 无包 A 命中，但 mandatory factory SC-1 端到端验收失败且存在 harness 未镜像 raw 锚点归档契约的 P1，不能批准。"
