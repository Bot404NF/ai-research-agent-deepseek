# 性能专项测试计划（待实现）

待覆盖冷启动与预热、单用户/并发、首内容延迟、P50/P95、失败率与 token 用量。报告固定数据集、环境、样本规模和调用顺序；失败请求单列，不混入成功延迟。

已有统计函数测试在 `tests/unit/test_acceptance.py`，历史测量在 `eval/results/`；它们不证明当前并发吞吐。压测入口预留在 `scripts/benchmark.py`。
