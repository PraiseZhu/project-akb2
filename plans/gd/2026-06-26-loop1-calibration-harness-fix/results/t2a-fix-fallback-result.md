# t2a-fix-fallback — Scrapling fallback 串联修复结果

## 任务

修复 reviewer P2 finding：`extract.py:433-435` 仅根据 `is_anti_bot` 二选一调用**一次** `_extract_via_scrapling`，普通页 adaptive 失败后未 fallback browser，直接落全失败签名。

## 修复实现

**文件**: `src/akb2/quality/intake/extract.py` — `extract_content` 分支 3 调用点

将单次 `use_browser = is_anti_bot` + 单次调用改为 `for use_browser in (False, True)` 串联循环：

1. **adaptive 先试**（`browser_mode=False`）→ 若返回空 dict 或 `word_count < MIN_CONTENT_LENGTH` → `continue` 试下一个
2. **browser fallback**（`browser_mode=True`）→ 同样判空/过短
3. content 足够长 → 填充 result + `_apply_feed_fallback` + `_validate_metadata`
   - 元数据齐全 → success return
   - 元数据缺失 → fail-closed return（不产 raw，不落签名，同原逻辑）
4. 两次都失败（空/过短）→ 跳出循环 → 分支 4 落签名

**未改动**：
- `_extract_via_scrapling` 本身（Fetcher.get / StealthyFetcher.fetch API）
- 抽取决策树（defuddle 分支、PDF 分支）
- `_validate_metadata` / `_apply_feed_fallback` / `ExtractResult` 结构
- `is_anti_bot` 仍用于 defuddle 分支跳过（反爬页跳 defuddle 直入 scrapling）

**行为变化**：普通页（`is_anti_bot=False`）adaptive 失败后现会再试 browser，而非直接签名。反爬页 adaptive 失败也会再试 browser（兜底）。

## 测试覆盖

**文件**: `tests/test_extract_scrapling_fallback.py` — 新增 (d) 组 2 个测试 + spy 辅助

- `test_adaptive_fail_falls_back_to_browser_success`
  - adaptive 抛错返回 {} → 断言 `scrapling_calls == [False, True]` → browser 成功返回长内容 → `method=SCRAPLING_BROWSER` + `extraction_success=True`
- `test_adaptive_and_browser_both_fail_fail_closed`
  - adaptive + browser 都抛错返回 {} → 断言 `scrapling_calls == [False, True]` → `method=FAILED` + 签名落账

spy (`_install_scrapling_spy`) 包装 `_extract_via_scrapling` 记录 `browser_mode` 调用序列，调用原函数保留真实行为。断言绑行为（调用序列 + method + result），不焊死实现。

## verify 真跑

```
$ pytest tests/test_extract_scrapling_fallback.py -v
Pytest: 6 passed
exit=0

$ ruff check src/akb2/quality/intake/extract.py tests/test_extract_scrapling_fallback.py
No issues found
```

**回归检查**：跑 extract 相关 4 个测试文件（`test_extract_scrapling_fallback.py` + `tests/quality/test_intake_extract.py` + `test_extraction_context.py` + `test_intake_orchestrator.py`）→ 76 passed, 2 failed。

2 个 failure 为**预先存在**（`git stash` 后仍失败，与本次改动无关）：
- `TestExtractViaScrapling.test_adaptive_mode_calls_fetcher`
- `TestExtractViaScrapling.test_browser_mode_calls_stealthy_fetcher`

原因：`tests/quality/test_intake_extract.py` 用 MagicMock 但未 mock `_check_tool_available` import gate，本机未装 scrapling → `_extract_via_scrapling` 返回 {} → `assert result` 非空失败。属于包 A / 环境问题，不在本任务写入权限内。

## SC 验收

- **SC-5 pass**: adaptive → browser fallback 串联已实现 + 测试覆盖（`scrapling_calls == [False, True]` 断言绑定调用序列 + 最终 method/result）。
