# 评测报告与审核归档

从 `eval/` 根目录迁入的各轮 `summary_*`、`review_*`、`reviews_*`、`acceptance_review*`、`v4_pro_*` 和 `final_checks*` 保留为历史证据。报告中的结果路径调整为 `../results/`，结构化 JSON 原文未改写。

历史 JSON 内的旧题集路径映射到 `eval/datasets/`，旧报告路径映射到 `eval/reports/`，旧测试路径见 `docs/directory-layout.md`。记录中的命令、代码摘要和时间是当时的快照，不表示当前代码与冻结版本一致。原始执行记录仍在 `eval/results/`。

## 正式留档规则

原始结果、审核、改分理由与结论成套保留；失败实验不因未采用而删除。当前项目的验证入口见[证据索引](../../docs/project/evidence.md)，清理清单见[项目收尾记录](../../docs/project-closeout-2026-11-01.md)。

日常 JUnit、临时诊断和重复检查输出写入被忽略的 `.security-artifacts/`、`eval/runtime/` 或 `work/`，正式验收再选择性留档。不要整体忽略 `eval/results/`。已被正式报告引用的历史记录保留；新产物提交前检查正文、私人路径与凭据。
