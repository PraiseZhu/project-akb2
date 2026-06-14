# Step 1: 数据契约与身份契约

GD_STANDARD: Project GD/prompts/gd-review-standard.md
GOAL_SOURCE: Project GD/docs/gd-v7-project-goal.md
TEMPLATE_KIND: gd-step-plan

日期: 2026-06-14 | 状态: draft | 负责人: Claude 执行; Codex cross-review

---

## 1. 目标链（继承 + 当前 task goal）

```text
PROJECT_GOAL: AKB2 内容质量循环（循环1）全链上线。
CHAIN_GOAL: 把抓取链路从占位 stub 接成生产可用，补全数据契约和幂等机制。
PHASE_GOAL: 见 master-plan（plans/gd/2026-06-14-cql-intake/master-plan.md §1）
TASK_GOAL: 定义 ExtractionContext dataclass（≥8 字段）；article_id = sha256(canonical_url)[:12]；extract_content 支持 feed 元数据 fallback；两者皆缺且判定失败时 fail-closed 不产 raw。
```

---

## 2. Review 对齐

- REVIEW_DOMAIN: `ai_infra`
- REVIEW_FOCUS: `ExtractionContext 字段完整性(F2); article_id 算法确定性(F3); fallback 边界条件与 fail-closed 行为(F2)`

---

## 3. 前置条件

- blocked_by: —（本步骤是所有后续 step 的地基）
- Hard-stop: ExtractionContext 字段 < 8 个 → 不进 step-2

---

## 4. 成功标准（SC）

- [ ] SC-1（数据契约）: ExtractionContext ≥8 字段；fallback 顺序 title/author/published；两者皆缺 fail-closed
  - verify (method: test): `cd "/Users/praise/AI-Agent/Claude/projects/Project AKB2" && python -m pytest tests/quality/test_extraction_context.py -q`
  - expect: exit 0; "extractor 缺 title 但 feed_title 存在→可归档；author/published 同理；两者皆缺 fail-closed 断言通过"

---

## 6. 实现步骤

```text
Step.1
  WHERE: src/akb2/quality/intake/models.py（新建）
  WHAT:  定义 ExtractionContext dataclass：source_slug/source_publisher/feed_title/feed_author/feed_published/url/content_type/content_hash；article_id 计算函数（sha256(canonical_url).hexdigest()[:12]）
  WHY:   F2 数据契约断裂；F3 article_id 唯一化
  VERIFY: python -c "from akb2.quality.intake.models import ExtractionContext; print(len(ExtractionContext.__dataclass_fields__))"

Step.2
  WHERE: src/akb2/quality/intake/extract.py:181（extract_content 函数）
  WHAT:  extraction_config 参数接收 ExtractionContext；fallback 顺序：`title ← extracted.title or context.feed_title`、`author ← extracted.author or context.feed_author`、`published ← extracted.published or context.feed_published`、`publisher ← extracted.publisher or context.source_publisher`；四者皆缺且 _validate_metadata 失败→不产 raw，落 fetch_fail 签名
  WHY:   F2/F3 数据契约；F-p2-3 feed_title fallback 缺失
  VERIFY: python -m pytest tests/quality/test_extraction_context.py -q

Step.3
  WHERE: tests/quality/test_extraction_context.py（新建）
  WHAT:  5 个 fixture 用例：① extractor 缺 title 但 feed_title 存在→可归档；② author/published 同理 fallback；③ 两者皆缺 fail-closed；④ ExtractionContext 字段完整性；⑤ article_id 确定性
  WHY:   SC-1 测试覆盖，F-p2-3 title fallback 用例
  VERIFY: python -m pytest tests/quality/test_extraction_context.py -q
```

---

## 7. 边界

修改: `src/akb2/quality/intake/models.py`(新建), `src/akb2/quality/intake/extract.py`(扩展)
新建测试: `tests/quality/test_extraction_context.py`
禁改: 消费侧模块（admission/distill/grader/repair/promote/runner/calibration），旧计划文件

---

## 8. 残余风险

- extract_content API 扩展可能破坏 test_intake_extract.py 现有断言 → hard-stop：step-1 结束后立刻跑 test_intake_extract.py，红则修复再进 step-2
