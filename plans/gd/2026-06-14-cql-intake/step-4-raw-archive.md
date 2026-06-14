# Step 4: raw 归档契约补全

GD_STANDARD: Project GD/prompts/gd-review-standard.md
TEMPLATE_KIND: gd-step-plan

日期: 2026-06-14 | 状态: draft

---

## 1. 目标链

```text
TASK_GOAL: raw 文件名加 article_id 短码；archive_from_extract_result 新增 source_metadata 参数；9 字段/§A-§Z/fail-closed 保留；failure 签名补 details/sample_error。
```

REVIEW_DOMAIN: ai_infra
REVIEW_FOCUS: `raw 文件名唯一性(F3); frontmatter 9 字段完整(F5); §A-§Z fail-closed(F5); failure 签名细节(P3)`

---

## 3. 成功标准（SC）

- [ ] SC-6（raw 归档 fail-closed）: frontmatter 9 字段齐；§A-§Z assert 全命中；任一不满足不产 raw 文件落 failure 签名；签名含 details/sample_error 字段
  - verify (method: test): `cd "/Users/praise/AI-Agent/Claude/projects/Project AKB2" && python -m pytest tests/quality/test_intake_raw.py -q`
  - expect: exit 0; "≥22 tests passed；frontmatter 缺字段→无文件、锚点未命中→无文件、failure 签名有 details 字段断言全过"

---

## 6. 实现步骤

```text
Step.1
  WHERE: src/akb2/quality/intake/raw_archiver.py（文件名生成逻辑，约 :374）
  WHAT:  文件名从 `<slug>--raw--<date>.md` 改为 `<slug>--<article_id>--raw--<date>.md`
  WHY:   F3 同源同日多篇文件名碰撞
  VERIFY: python -m pytest tests/quality/test_intake_raw.py -q -k "filename"

Step.2
  WHERE: src/akb2/quality/intake/raw_archiver.py（archive_from_extract_result:408）
  WHAT:  新增 source_metadata: ExtractionContext | None 参数；显式从 source_metadata 填 frontmatter（source_slug/source_publisher/source_authors/source_published_at 优先从 metadata 取）
  WHY:   F2 archive 显式接收 source metadata；F5 frontmatter 9 字段需有值来源
  VERIFY: python -m pytest tests/quality/test_intake_raw.py -q -k "source_metadata"

Step.3
  WHERE: src/akb2/quality/intake/raw_archiver.py（failure 签名写入逻辑）
  WHAT:  failure 签名 dict 补 details/sample_error 字段（抓取失败时 sample_error=首行错误；归档失败时 details=缺失字段列表）
  WHY:   P3 排障慢
  VERIFY: python -m pytest tests/quality/test_intake_raw.py -q -k "failure_signature"

Step.4
  WHERE: tests/quality/test_intake_raw.py（补测试）
  WHAT:  补 source_metadata 传入测试；failure 签名 details/sample_error 字段断言
  WHY:   SC-6 覆盖新改动
  VERIFY: python -m pytest tests/quality/test_intake_raw.py -q
```
