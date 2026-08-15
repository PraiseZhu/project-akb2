# AKB2 主链路入库机制重构：配置漂移修复 + 拆除 repair/驻留/双模式 + 三车道化

## 意图契约

<!-- pr-intent:start -->
目标: 按 vault 入库门控和 wiki 层标准反推，拆掉过复杂的主链路入库机制：先修 F0 配置漂移让已过闸内容能进 wiki，再删除 repair/驻留/双模式，把准入和评分收到车道化的短路径，并扩到 tutorial/pattern/design 三车道，目标是内容质量可判的同时把入库率从约 4% 拉起来。
优先级: p0-live-promote-parity / p1-drop-repair-residency / p2-single-active-path / p3-admission-one-call / p4-grader-floor-lane / p5-three-lanes
非目标: 不新增 flip/bypass；31 篇存量只走既有 requeue.py；不改 CI；不向 vault 写 .py/.sh
验收: wave 1–3 各组 SC 已 verified；intent-check status ∈ {OK, REBUILT}；size/format 闸对 candidate HEAD 绑定
<!-- pr-intent:end -->

## 变更说明

本 PR 按 vault 入库门控与 wiki 层标准反推，拆除主链路过复杂的入库机制，分四个 wave 落地：

### Wave 1 — F0 配置漂移修复（p0-live-promote-parity）
修复 live 链路与 wiki 层的配置漂移，使已过闸内容能真正进入 wiki。live promote 路径与验收基线对齐（parity），不再因配置不一致把已过闸内容卡在库外。

### Wave 2 — 拆除 repair / 驻留 / 双模式（p1-drop-repair-residency / p2-single-active-path）
删除 repair 定点修复循环、驻留（residency）机制与双模式运行路径，收敛为单一活跃路径。入库链路从「修复循环兜底」转为「准入即判定」，消除形状相似但停止条件不同的循环并存问题。

### Wave 3 — 准入单次调用 + 评分车道化底线（p3-admission-one-call / p4-grader-floor-lane）
准入（admission）收敛为单次调用完成判定；grader 评分按车道设定底线（floor），内容质量可判且判定成本可控。

### Wave 4 — 扩展 tutorial / pattern / 三车道（p5-three-lanes）
把入库短路径从单一内容类型扩到 tutorial / pattern / design 三车道，各车道独立门控标准。

### 存量处理边界
31 篇存量内容不回流新链路，只走既有 `requeue.py`；不新增任何 flip/bypass 开关；CI 不改动；不向 vault 写 `.py`/`.sh`（constitution 原则 5 解耦约束）。

## 验收证据

- wave 1–3 各组 SC 已 verified（详见 approve-exec ledger 对应组交付记录）
- intent-check 对本 PR body 的 marker 区块与 `.pr-intent.md` 工作副本校验 status ∈ {OK, REBUILT}
- size / format 闸对 candidate HEAD 绑定
