# Fixture 04 — edge: P3 风险不能升级为 blocker

## Capsule

```
REVIEW_KIND: code
REVIEW_DOMAIN: app_code
PLAN_REVIEW_ALIGNMENT: 字段齐
PLAN_ALIGNMENT_PRESENT: true
REVIEW_FOCUS: 命名风格;注释完整性;测试覆盖
REVIEW_FOCUS_SOURCE: plan
REVIEW_MODE: single_pass
REVIEW_ROUND: initial
REVIEW_DELTA_SCOPE: full_matrix
PREVIOUS_FINDINGS: 无
ONE_REVIEW_ONLY: true
PROJECT_ROOT: /tmp/p1b-residual-fixture
REPO_ROOT: 无
BRANCH: 无
BASELINE: 单 Python 文件新增
PROJECT_GOAL: 写一个 add(a, b) 加法函数
PROJECT_GOAL_SOURCE: user_latest
CURRENT_TASK_GOAL: 接两个 int,返回和
SUCCESS_CRITERIA: add(2, 3) == 5;输入非 int 时 raise TypeError
IN_SCOPE: utils/math_helper.py
OUT_OF_SCOPE: 别的算术函数
USER_ACCEPTED_DECISIONS: 单文件即可
CHANGESET_OR_PLAN: |
  新建 utils/math_helper.py:

  def add(a: int, b: int) -> int:
      """Return a + b."""
      if not isinstance(a, int) or not isinstance(b, int):
          raise TypeError("add() expects int")
      return a + b

  unit test:
  def test_add():
      assert add(2, 3) == 5
      try:
          add("a", 1)
      except TypeError:
          pass

  注:命名风格用了 add 而不是 add_two_ints,可能有人觉得不够 verbose。注释少。无 docstring example。
VALIDATION_EVIDENCE: |
  pytest test_add::test_add 通过。手测 add(2, 3) = 5。
KNOWN_LIMITATIONS: 边界扩展(浮点 / Decimal / overflow)未覆盖
BASELINE_CONFIDENCE: high
REVIEW_RULES: ~/.claude/commands/review.md §审查标准
```

## Expected verdict

- VERDICT: APPROVED
- 结论:**通过**
- 0 P1/P2 finding(代码功能正确 + 类型校验已加 + 测试已加)
- §剩余风险 可列 P3(命名 verbose / docstring example / 浮点支持等),但**不能**升 blocker
- 验证 cc-review 是否守住"P3 不阻塞"边界
- 输出在单一 fenced markdown 内
