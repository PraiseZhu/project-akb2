verdict: APPROVED
findings: []
summary: "复审通过：上轮 3 个 findings 已闭合；Scrapling 串联测试 6 passed，calibration diagnostics 31 passed，ruff passed；真实 calibration 完整重跑 `calib_exit=1`（已知 factory_acceptance=False 非包 B 缺陷），lead 指定 compose_self_gate 计数为 0，且全字段额外扫描确认 `引用的锚点` / `§A 在 raw 文件中不存在` 均为 0。报告中仍有 7 条非锚点类 `compose_self_gate_failed`（SG-how-density / SG-must-cover / SG-command-recheck），属于下游质量门失败，不是本轮 P1 锚点 harness 回归。"
verification:
  - "pytest tests/test_extract_scrapling_fallback.py -q -> 6 passed"
  - "pytest tests/test_calibration_harness_diagnostics.py -q -> 31 passed, 2 warnings"
  - "PYTHONPATH=src python3 -m akb2.quality.calibration --corpus evals/main-pipeline-quality/corpus.yaml --report-out reports/calibration/factory.json ; echo calib_exit=$? -> calib_exit=1, factory_acceptance=False, live_positive_intake_raw_failures=0"
  - "lead 指定 compose_self_gate 命令 -> compose_self_gate_failed= 0"
  - "额外全字段扫描 -> 引用的锚点=0, §A 在 raw 文件中不存在=0"
  - "ruff check src/akb2/quality/calibration.py src/akb2/quality/intake/extract.py -> All checks passed"
checked:
  - "src/akb2/quality/calibration.py:_compose_raw_content 复用 raw_archiver._inject_anchors / ANCHOR_DEFINITIONS / _assert_anchors，并生成 `## 锚点目录` + anchored_content；fixture 路径也经 _inject_anchors_into_raw 注入锚点。"
  - "src/akb2/quality/intake/extract.py:extract_content 使用 `for use_browser in (False, True)`，adaptive 空/短内容后继续 browser fallback。"
  - "src/akb2/quality/calibration.py:_classify_failure_class 无 preflight 时对 `All extraction methods failed` 归 `extraction_failure`，不再误判 dead_link。"
