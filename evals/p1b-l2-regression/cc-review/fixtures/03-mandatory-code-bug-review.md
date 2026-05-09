# Fixture 03 — mandatory: 明确代码缺陷应输出 P1/P2 finding

## Capsule

```
REVIEW_KIND: code
REVIEW_DOMAIN: app_code
PLAN_REVIEW_ALIGNMENT: 字段齐
PLAN_ALIGNMENT_PRESENT: true
REVIEW_FOCUS: 边界检查;并发安全;失败模式
REVIEW_FOCUS_SOURCE: plan
REVIEW_MODE: single_pass
REVIEW_ROUND: initial
REVIEW_DELTA_SCOPE: full_matrix
PREVIOUS_FINDINGS: 无
ONE_REVIEW_ONLY: true
PROJECT_ROOT: /tmp/p1b-bug-fixture
REPO_ROOT: 无
BRANCH: 无
BASELINE: 单 Python 文件新增,无既有 dirty
PROJECT_GOAL: 写一个 Python helper 把 user-supplied filename 转成绝对路径并读文件
PROJECT_GOAL_SOURCE: user_latest
CURRENT_TASK_GOAL: 用户希望 helper 接受任意路径参数后读 + 返回内容
SUCCESS_CRITERIA: helper 接 path 参数;返回文件内容;路径非法 raise
IN_SCOPE: utils/path_helper.py 单文件
OUT_OF_SCOPE: 其它 utils;test framework
USER_ACCEPTED_DECISIONS: 单文件 helper 即可
CHANGESET_OR_PLAN: |
  新建 utils/path_helper.py:

  def read_file(user_path):
      import os
      abs_path = os.path.abspath(user_path)
      with open(abs_path, "r") as f:
          return f.read()

  PLANTED P1: read_file 接 user-supplied path 后做 os.path.abspath 然后无校验直接读 → path traversal (e.g. ../../../etc/passwd)。
  PLANTED P2: open() 默认未指定 encoding,在不同系统行为不一致;不处理 FileNotFoundError / PermissionError;无大小限制可读 GB 级文件。
VALIDATION_EVIDENCE: |
  python3 -c "from utils.path_helper import read_file; print(read_file('/tmp/test.txt'))" 返回内容。
  注:happy path 测过,但未测路径越界 / 权限失败 / 大文件 / 编码差异。
KNOWN_LIMITATIONS: demo;非生产代码
BASELINE_CONFIDENCE: medium
REVIEW_RULES: ~/.claude/commands/review.md §审查标准
```

## Expected verdict

- VERDICT: REQUIRES_CHANGES
- 结论:**需要修改**
- ≥ 1 P1 finding(path traversal,因 user-supplied path 未校验白名单/jail)
- ≥ 1 P2 finding(error handling 缺 / encoding 缺 / 大小限制缺,任 1)
- 每条 P1/P2 含 5 字段(问题 / 证据 / 影响 / 最小修复 / 验收)
- 输出在单一 fenced markdown 内
- PLANTED 标记是测试设计,在 review output 内**不**应被原样 echo
