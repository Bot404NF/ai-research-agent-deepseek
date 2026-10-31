# 验证与证据

以下主要对应 2026-11-01 的既有验收，不代表后续代码自动经过相同真实验证。最新整理与检查见[项目收尾记录](../project-closeout-2026-11-01.md)。

| 可说明的能力或结论 | 正式证据 | 验证边界 |
|---|---|---|
| 最新网页手测及失败定位 | [手测原报告](../../eval/results/manual_research_20261101_f5d2b376.md)、[证据摘录](../../eval/results/manual_research_20261101_f5d2b376.evidence.json)、[复核](../../eval/reports/manual_research_review_20261101.md) | 内容完整性未通过；漏整理本、卷数归纳不清，初稿差异保留待核对 |
| 统一100题、性能成本对照与候选未采用 | [收口报告](../../eval/reports/interview_completion_20261101.md)、[逐题结果](../../eval/results/interview_live_20261101.json)、[汇总](../../eval/results/interview_summary_20261101.json) | 固定开发题集、Flash、AI辅助审核，不代表全库质量 |
| 审核保留原始判断及改分理由 | [原始审核](../../eval/reports/interview_ai_reviews_20261101.json)、[二次审核](../../eval/reports/interview_ai_reviews_checked_20261101.json) | 没有完成真人专家审核或扫描原件校勘 |
| 演示案例已知内容修复 | [修订展示稿](../../eval/results/interview_demo_corrected_20261101.md)、[原始模型复验](../../eval/results/interview_demo_repaired_20261101.json)、[复核说明](../../eval/reports/interview_demo_fix_review_20261101.md) | 复用原证据，仅检查与报告两节点；修订稿含辅助编辑，不代表全流程重跑或全库质量改善 |
| 研究计划跨进程恢复、报告读取及下载 | [运行记录](../../eval/results/interview_demo_20261101.json)、[原始报告](../../eval/results/interview_demo_20261101.md)、[内容复核](../../eval/reports/interview_demo_review_20261101.md) | 流程通过，内容仍有来源描述缺陷；不保证调用恰好一次 |
| 当轮离线回归与最终检查 | [JUnit](../../eval/results/interview_tests_20261101.xml)、[检查记录](../../eval/results/interview_final_checks_20261101.json) | 当轮194项通过、1项排除；不能代替真实模型、索引与联网验证 |
| 异常脱敏与凭据隔离 | [防泄露说明](../secret-protection.md)、[验证记录](../secret-protection-validation-2026-10-31.md) | 属于记录日期及测试覆盖范围，不等于所有数据安全问题已解决 |
| 失败实验与未采用取舍 | [第四周报告](../../eval/reports/week4_completion_20261031.md)、[实验入口](../../eval/README.md) | 历史成绩与当前模型、路径和默认配置分开说明 |

正文、引用和审核保留在原评测目录，本页只维护索引，不复制原始数据或改写答案。
