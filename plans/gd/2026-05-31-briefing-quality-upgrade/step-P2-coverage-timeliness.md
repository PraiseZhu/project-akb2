# Step P2: 覆盖与时效（zh 抓取 + 语义去重 + engagement velocity）v1

GD_STANDARD: Project GD/prompts/gd-review-standard.md
GOAL_SOURCE: Project AKB2 — 社交日报系统
TEMPLATE_KIND: gd-step-plan

日期：2026-05-31
状态：draft
负责人：Claude 执行

---

## 1. 目标链

```text
PROJECT_GOAL: 真实体现 AI 社区高质量内容，且高质量内容第一时间被捕获
CHAIN_GOAL:   扩展覆盖（含中文社区）+ 强化时效信号
PHASE_GOAL:   ref master-plan §1
TASK_GOAL:    真正抓 zh 大号并合并；novelty 改语义近似去重；engagement 加涨赞速度维度
```

---

## 2. Review 对齐

- REVIEW_DOMAIN：`app_code`
- REVIEW_FOCUS：en+zh 合并正确; 语义去重不误杀异质内容; velocity 计算正确
- Domain notes：P2-2 复用 P1-1 嵌入客户端做近似去重；P2-1 与 P3a 已做的 lang 分流配套（抓取层补齐）。

---

## 3. 前置条件

- blocked_by：`P1-1`（P2-2 依赖嵌入客户端）；P2-1/P2-3 无前置
- 必须 artifact：`embeddings.py`（P2-2）
- Hard-stop：zh 账号未复验产量 → 先验证再纳入；改 scoring 需授权

---

## 4. 成功标准（SC）

- [ ] SC-P2-a：抓取层 fetch en+zh 合并（master SC-10）
  - verify (test): `test_account_collector` 断言 collect 含 en+zh
  - expect: 绿；真跑含中文大号内容
- [ ] SC-P2-b：novelty 语义近似去重（master SC-11）
  - verify (test): 改写转发同义推文 novelty=0
  - expect: 绿
- [ ] SC-P2-c：engagement 加 velocity（master SC-12）
  - verify (test): 高速新帖 > 同总量老爆款
  - expect: 绿

---

## 5. 非目标

- 发现层突破固定名单 / 实时哨兵 → 产品边界决策，另立计划
- 不改 gate / 调度时间

---

## 6. 边界与失败模式

- zh 账号低产/被限流 → 复用 reserve 补抓逻辑；pacing 控在安全区（实测 ~34 触 429）
- 语义去重阈值过低 → 误杀同主题不同角度内容；阈值需保守（cosine>0.9 才判重）+ eval 验证
- velocity 需 created_at 精确到小时；缺失时回退累计值
