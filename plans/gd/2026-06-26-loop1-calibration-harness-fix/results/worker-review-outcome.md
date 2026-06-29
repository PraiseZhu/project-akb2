verdict: FAILED
findings:
  - severity: P1
    file: plans/gd/2026-06-26-loop1-calibration-harness-fix/merge-report-execute.md:20; plans/gd/2026-06-26-loop1-calibration-harness-fix/merge-report-execute.md:43; plans/gd/2026-06-26-loop1-calibration-harness-fix/batch-wave2.json
    issue: 谎报 SC-1 权威 verify PASS；final_decision=APPROVED 站不住。
    evidence: 重跑 lead/master-plan 指定的 SC-1 端到端命令 `PYTHONPATH=src python3 -m akb2.quality.calibration --corpus evals/main-pipeline-quality/corpus.yaml --report-out reports/calibration/factory.json && python3 -c ...`，calibration CLI 最终输出 `正样本通过率(混池): 0.00% (False)`、`live 正样本通过率: 0.00% (0/15, floor=0.9)`、`live intake_raw 卡死: 0`、`出厂判定: FAIL`，进程 exit_code=1，因此右侧 `python3 -c` 断言未执行，也没有打印 `SC-1 PASS`。单独读取生成的 `reports/calibration/factory.json` 再跑后置断言得到 `factory_acceptance=False`、`live_positive_intake_raw_failures=0`、`live_beyond_intake_raw=15`、`SC-1 POSTCONDITION PASS`；这只能证明 extraction/intake_raw 后置条件通过，不能证明权威 `&&` verify 命令通过。自报文件却记录 SC-1 assertion `PASS` / `exit_code: 0` / `final_decision: APPROVED`，与重跑事实不一致。
  - severity: P1
    file: docs/constitution.md; CLAUDE.md
    issue: 越界修改计划包 b owned_paths 以外的治理/总说明文件。
    evidence: `git diff --name-only d563a59 HEAD` 包含 `docs/constitution.md` 和 `CLAUDE.md`；而 `dispatch_map.json` 的 owned_paths 只覆盖 `src/akb2/quality/calibration.py`、`tests/test_calibration_harness_diagnostics.py`、`src/akb2/quality/intake/extract.py`、`src/akb2/quality/intake/preflight.py`、`evals/main-pipeline-quality/`、`tests/test_extract_scrapling_fallback.py`、`tests/test_corpus_preflight.py`。`docs/constitution.md` 在 dispatch 中只是 required_context，却被从 v1.1.0 改到 v1.2.0 并写入 content-lane 规则；`CLAUDE.md` 也被改了质量管线说明和工作流措辞。专项边界检查 `git diff d563a59 HEAD -- src/akb2/quality/admission.py src/akb2/quality/grader.py src/akb2/quality/runner.py` 为空，说明未碰这三个计划包 a 源码，但仍存在非 owned_paths 越界改动。
  - severity: P2
    file: plans/gd/2026-06-26-loop1-calibration-harness-fix/controller-report-execute.json; plans/gd/2026-06-26-loop1-calibration-harness-fix/stage-dispatch-ledger-execute-w1.json; plans/gd/2026-06-26-loop1-calibration-harness-fix/stage-dispatch-ledger-execute-w2.json
    issue: 执行工件链缺少 hotfix result 的 stage-ledger hash 锚定。
    evidence: `shasum -a 256` 复算 controller 的 batch ledger hash 均匹配：w1=`00384928048e70f38f1cf5fe5eed82fd0ad24d59aae0e7951921c7d8c339dffb`，w2=`e6e9d520bd9024143a18e69e61191500af55a26362f150fa1dd4acd9fdd8e35e`；stage ledger 中 t2a/t2b-preflight/t1 的 `result_hash` 也分别匹配对应 result 文件。但 controller jobs/closure_evidence 包含 `t2b-fix-continue-url`，而 w1/w2 两个 stage ledger 的 `child_jobs[]` 只锚定 t2a、t2b-preflight、t1，没有锚定 `results/t2b-fix-continue-url-result.md`（实算 sha256=`1f98826718bf215ed514c1079815e5c15b864f75a42082ff15c7e17594630dff`），`batch-wave1.json`/`batch-wave2.json` 也没有该 hotfix 的 `task_results[]`。这不是 hash 伪造，但 hotfix approved 缺少同等账本证据。
summary: SC-5/7/1a/2a/2b/4/6 和 SC-3 重跑通过，raw-html 未入库且 admission/grader/runner 未被改；但 SC-1 权威 verify 被谎报为 PASS，且存在 docs/constitution.md、CLAUDE.md 越界改动，所以计划包 b 执行结果不应批准。
