# 业务质量接入交接

用户已明确指定由聊天《讲解第四周任务》（01a19177-afd7-7512-9166-0592983d0faa）负责后续接入，本聊天停止重叠文件修改。工作目录共享，以下改动均已留在工作区；没有回滚、提交或推送本轮接入修改。

## 本聊天的修改

- `evidence_policy.py`：共用GROUNDING_GUIDANCE、连续编号拆分、轮流合并去重；包含历史拼接的“后续问题”时不再次按历史编号拆分。
- `retrieval.py`：在retrieve_library中启用分题、书目范围和内容排序；retrieve_versions新增candidate_k/source_paths，仅扩大候选读取预算，目录不足时的扩展门槛仍按最终top_k计算，避免12候选门槛挤掉指定目录。
- `answer.py`：保留BASE_SYSTEM_PROMPT/BASE_EVIDENCE_GUIDANCE，业务SYSTEM_PROMPT/EVIDENCE_GUIDANCE附加共用约束；agent原有引用EVIDENCE_GUIDANCE的路径会自动取得该约束。workflow.GUIDANCE也附加同一约束。
- `evaluation/target.py`、`quality.py`、`baseline.py`：明确使用BASE_SYSTEM_PROMPT，避免业务提示词更新使历史对照漂移或重复追加约束。
- `tests/integration/test_business_quality.py`：6项业务入口测试，包含CLI、普通问答、多轮、研究节点、增量/版本与分题预算、目录扩展门槛。
- `eval/verify_business.py`：真实只读业务检索，以及隔离CLI/HTTP会话/研究任务冒烟。测试状态检查与代码摘要前后对比已经加入。
- `.github/workflows/ci.yml`新增上述文件检查；`test_retrieval_quality.py`仅使mock接受新关键字参数。未覆盖此前模型、安全与其他修改。

## 验证结果及适用范围

1. 定向业务入口6项通过；完整工作区185通过、1排除，记录`eval/results/business_tests_20261031.xml`。运行时另一聊天也在改同一文件，因此这不是最终冻结代码的保证，合并后应重新验证。
2. `eval/results/business_integration_20261031.json`保存100题真实retrieve_library检索：100成功，100题最终证据与统一评测候选完全相同，整题锚点命中76/82。没有重新生成100份答案，不重用90.24%作为新业务正确率。
3. 首轮CLI真实回答正确给出《上海地方志概述》1985年3月和长春邮电学院印刷厂，原始输出在同一JSON的smoke.cli。
4. HTTP首个中文请求因验证脚本的UnicodeEncodeError中断，未创建研究任务。已修复为UTF-8字节请求体，但尚未重新运行付费冒烟。隔离服务使用随机端口和eval/runtime下新会话目录，没有动用户原有会话或研究任务。
5. 首轮结果必须保留completed_with_errors，不能改称HTTP/研究通过。脚本语法与成功/失败状态判定已做离线检查。

## 必须合并的并行实现

另一个聊天在grounded_retrieval中加入retrieve_business、在config中加入grounded_answers开关，并把agent绑定到retrieve_business。当前retrieve_library又已被本聊天改成无条件新策略，因此“开关为false时调用retrieve_library”目前并不能退回原策略。

另外，agent现在可能通过EVIDENCE_GUIDANCE取得一次GROUNDING_GUIDANCE，同时又按grounded_answers追加一次。合并时需统一入口及开关语义，避免重复提示词和双重重排；保留其他聊天对追问建议、费用账本及性能的修改。不要整文件覆盖、回滚或丢弃现有工作。

建议先确定唯一业务入口/旧路径和单一提示词来源，再重跑入口回归、增量/目录/历史/许可测试及真实冒烟。verify_business脚本默认直接验证retrieve_library；如最终选择retrieve_business作为唯一入口，应同步更新验证脚本，确保测的是实际业务函数。

另一个聊天有用户授权的10元API预算。本聊天首轮冒烟只成功完成一次CLI回答，未保存其usage/费用，未进入另一个聊天账本；不能当作零费用。后续按该聊天预算规则继续，不需要重跑已完成的100题只读检索。验证脚本没有断点续跑，直接重新--smoke会再次调用CLI及模型，需自行选择新输出文件并计入用量。
