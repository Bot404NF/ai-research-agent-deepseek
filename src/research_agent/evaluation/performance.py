"""第四周真实业务计时与持久费用上限；默认入口只校验。"""

import argparse
import json
import time
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from research_agent.config import PROJECT_ROOT, get_settings
from research_agent.evaluation.acceptance import digest, distribution
from research_agent.evaluation.baseline import answer_checks, token_cost, validate_dataset
from research_agent.evaluation.target import review_template
from research_agent.security import error_type, protect_external_call
from research_agent.storage import write_json_atomic

PRICING = {
    "model": "deepseek-flash",
    "currency": "CNY",
    "source_url": "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/",
    "access_date": "2026-10-31",
    "input_hit_per_million": 0.02,
    "input_miss_per_million": 1,
    "output_per_million": 4,
}
PEAK = {
    **PRICING,
    "input_hit_per_million": 0.04,
    "input_miss_per_million": 2,
    "output_per_million": 8,
}


class BudgetStop(RuntimeError):
    """预算不足、用量未知或请求结果不确定时停止后续付费请求。"""


class Budget:
    def __init__(self, path, limit=10):
        self.path = Path(path)
        self.data = (
            json.loads(self.path.read_text(encoding="utf-8"))
            if self.path.exists()
            else {
                "limit_cny": limit,
                "pricing": PRICING,
                "calls": [],
            }
        )
        if self.data["limit_cny"] != limit or not 0 < limit <= 10:
            raise ValueError("预算必须为不超过10元且与原账本一致。")

    def reserve(self, messages, tools=()):
        from langchain_core.messages import messages_to_dict
        from langchain_core.utils.function_calling import convert_to_openai_tool

        payload = {
            "messages": messages_to_dict(messages),
            "tools": [convert_to_openai_tool(t) for t in tools],
        }
        if len(json.dumps(payload, ensure_ascii=False).encode()) > 100_000:
            raise BudgetStop("输入超出费用预留的100KB边界。")
        if any(
            c["state"] not in {"settled", "interrupted_reserved", "external_reserved"}
            for c in self.data["calls"]
        ):
            raise BudgetStop("存在用量未知或未完成请求，停止继续计费。")
        if (
            sum(c["reserved_or_peak_cny"] for c in self.data["calls"]) + 0.5
            > self.data["limit_cny"]
        ):
            raise BudgetStop("10元预算剩余额度不足以预留下一次请求。")
        call = {
            "state": "reserved",
            "reserved_or_peak_cny": 0.5,
            "started_at": datetime.now(UTC).isoformat(),
        }
        self.data["calls"].append(call)
        write_json_atomic(self.path, self.data)
        return call

    def settle(self, call, response):
        usage = response.usage_metadata
        peak = token_cost(usage, PEAK)["estimated_cost"]
        call.update(
            usage=usage,
            normalized_cost_cny=token_cost(usage, PRICING)["estimated_cost"],
            finish_reason=response.response_metadata.get("finish_reason"),
        )
        if peak is None or peak > 0.5:
            call["state"] = "unknown_usage"
            write_json_atomic(self.path, self.data)
            raise BudgetStop("用量未知或超出预留，停止后续付费请求。")
        call.update(state="settled", reserved_or_peak_cny=peak)
        write_json_atomic(self.path, self.data)


class MeasuredModel:
    """代理真实模型；所有工具循环与建议调用共用同一个费用账本。"""

    def __init__(self, model, budget, calls, tools=()):
        self.model, self.budget, self.calls, self.tools = model, budget, calls, tools

    def bind_tools(self, tools):
        return MeasuredModel(self.model.bind_tools(tools), self.budget, self.calls, tools)

    def invoke(self, messages):
        call = self.budget.reserve(messages, self.tools)
        self.calls.append(call)
        response = self.model.invoke(messages)
        self.budget.settle(call, response)
        return response

    def stream(self, messages):
        call = self.budget.reserve(messages, self.tools)
        self.calls.append(call)
        response = None
        for chunk in self.model.stream(messages):
            response = chunk if response is None else response + chunk
            yield chunk
        if response is None:
            raise BudgetStop("流式请求没有返回用量。")
        self.budget.settle(call, response)


def model_factory(settings, budget, calls):
    from langchain_openai import ChatOpenAI

    return MeasuredModel(
        ChatOpenAI(
            model=settings.chat_model,
            api_key=settings.api_key,
            base_url=settings.base_url,
            temperature=0,
            timeout=60,
            max_retries=0,
            max_tokens=4096,
            stream_usage=True,
            extra_body={"thinking": {"type": "disabled"}},
        ),
        budget,
        calls,
    )


def business_turn(case, settings, budget, store, session_id):
    from research_agent import agent
    from research_agent.grounded_retrieval import retrieve_business
    from research_agent.observability.runtime import ObservedModel

    calls, evidence, events = [], [], []
    retrieval_time = 0

    def search(question, settings, top_k=None):
        nonlocal retrieval_time
        started = time.perf_counter()
        found = retrieve_business(question, settings, top_k)
        retrieval_time += time.perf_counter() - started
        offset = len(evidence)
        evidence.extend(asdict(replace(e, label=f"S{offset + i}")) for i, e in enumerate(found, 1))
        return found

    started = time.perf_counter()
    first, last = None, None
    arm = {"status": "error", "api_attempted": False, "retrieved": evidence, "calls": calls}
    with (
        patch.object(agent, "_model", lambda s: ObservedModel(model_factory(s, budget, calls))),
        patch.object(agent, "retrieve_library", search),
    ):
        middleware_execute = agent.ToolExecutionMiddleware.execute

        def readonly_execute(self, name, arguments):
            if name in {"add_file_to_rag", "search_web"}:
                raise ValueError("性能验收只允许只读本地工具。")
            return middleware_execute(self, name, arguments)

        try:
            with patch.object(agent.ToolExecutionMiddleware, "execute", readonly_execute):
                for event in agent.stream_answer_turn(
                    case["question"], settings, session_id, store, decline_web_search=True
                ):
                    events.append(event)
                    if event.kind == "token" and event.text:
                        first = time.perf_counter() - started if first is None else first
                        last = time.perf_counter() - started
            answer = "".join(e.text for e in events if e.kind == "token")
            replacements = [e.text for e in events if e.kind == "replace"]
            if replacements:
                answer = replacements[-1]
            suggestions = next(
                (list(e.suggestions) for e in events if e.kind == "suggestions"), None
            )
            completed = suggestions is not None and not any(e.kind == "error" for e in events)
            arm.update(
                answer=answer,
                suggestions=suggestions,
                status="ok" if completed else "error",
                finish_reason="stop"
                if completed
                and all(c.get("finish_reason") in {"stop", "tool_calls"} for c in calls)
                else "incomplete",
                checks=answer_checks(answer, evidence),
            )
        except Exception as exc:  # noqa: BLE001 - 保留类别，不泄露外部错误正文
            arm.update(error_type=error_type(exc))
    arm.update(
        total_seconds=time.perf_counter() - started,
        first_token_seconds=first,
        last_token_seconds=last,
        retrieval_seconds=retrieval_time,
        api_attempted=bool(calls),
    )
    return arm


def performance_summary(rows):
    result = {}
    for name in ("baseline", "candidate"):
        arms = [r["arms"][name] for r in rows if name in r["arms"]]
        good = [a for a in arms if a["status"] == "ok"]
        calls = [c for a in arms for c in a["calls"]]
        result[name] = {
            "attempted": len(arms),
            "failed": len(arms) - len(good),
            "truncated": sum(c.get("finish_reason") == "length" for c in calls),
            "model_calls": len(calls),
            "normalized_cost_cny": sum(c["normalized_cost_cny"] for c in calls)
            if all(c.get("normalized_cost_cny") is not None for c in calls)
            else None,
            **{
                key: distribution([a[key] for a in good if a.get(key) is not None])
                for key in (
                    "total_seconds",
                    "first_token_seconds",
                    "last_token_seconds",
                    "retrieval_seconds",
                )
            },
        }
    return result


@protect_external_call
def main():
    from research_agent.conversation import ConversationStore

    parser = argparse.ArgumentParser(description="第四周：默认只校验，--run执行10元内真实业务对照")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--ledger", type=Path, default=PROJECT_ROOT / "eval/results/week4_budget_20261031.json"
    )
    args = parser.parse_args()
    if args.output.exists():
        parser.error("结果已存在，拒绝覆盖。")
    settings = replace(
        get_settings(), chat_model="deepseek-flash", web_search_enabled=False, grounded_answers=True
    )
    dataset_path = PROJECT_ROOT / "eval/datasets/unified.json"
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "status": "validated_only",
        "dataset_sha256": digest(dataset_path),
        "validation": validate_dataset(dataset, settings.data_dir),
        "settings": {
            "model": settings.chat_model,
            "top_k": settings.top_k,
            "max_tokens": 4096,
            "max_retries": 0,
            "grounded_answers": True,
            "followup_modes": {"baseline": "model", "candidate": "local"},
        },
        "code_sha256": {
            n: digest(PROJECT_ROOT / f"src/research_agent/{n}.py")
            for n in (
                "agent",
                "config",
                "grounded_retrieval",
                "retrieval",
                "answer",
                "evidence_policy",
                "conversation",
                "rag_updates",
                "evaluation/performance",
            )
        },
        "pricing": PRICING,
        "results": [],
    }
    from research_agent.agent import AGENT_PROMPT

    report["agent_prompt"] = AGENT_PROMPT
    write_json_atomic(args.output, report)
    if not args.run:
        print("100题及源文件摘要已校验；未访问服务。")
        return
    budget = Budget(args.ledger)
    store = ConversationStore(args.output.parent / (args.output.stem + "_conversations"))
    # 冷/首次业务调用同样计费，但不混入预热分位数。
    report["cold_first_turn"] = business_turn(
        dataset["cases"][0], replace(settings, followup_mode="model"), budget, store, "cold"
    )
    write_json_atomic(args.output, report)
    if report["cold_first_turn"]["status"] != "ok":
        report["status"] = "cold_failed"
        write_json_atomic(args.output, report)
        print("首次业务调用失败，停止后续运行，请检查原始记录。")
        return
    for index, case in enumerate(dataset["cases"]):
        row = {
            "id": case["id"],
            "question": case["question"],
            "stratum": case["stratum"],
            "should_abstain": case["should_abstain"],
            "arms": {},
        }
        report["results"].append(row)
        for name in ("baseline", "candidate") if index % 2 else ("candidate", "baseline"):
            row["arms"][name] = business_turn(
                case,
                replace(settings, followup_mode="model" if name == "baseline" else "local"),
                budget,
                store,
                f"{case['id']}-{name}",
            )
            report["status"] = "running"
            write_json_atomic(args.output, report)
            if any(
                c["state"] not in {"settled", "interrupted_reserved", "external_reserved"}
                for c in budget.data["calls"]
            ):
                report["status"] = "stopped_unknown_usage"
                write_json_atomic(args.output, report)
                print("请求用量不确定，已停止；原始结果与预留费用保留。")
                return
        print(f"{case['id']} 两组已保存", flush=True)
        if any(a.get("error_type") == "BudgetStop" for a in row["arms"].values()):
            report["status"] = "budget_stopped"
            write_json_atomic(args.output, report)
            return
    after = {n: digest(PROJECT_ROOT / f"src/research_agent/{n}.py") for n in report["code_sha256"]}
    report.update(
        status="completed",
        performance=performance_summary(report["results"]),
        code_sha256_after=after,
        code_unchanged=after == report["code_sha256"],
    )
    write_json_atomic(args.output, report)
    write_json_atomic(args.output.with_suffix(".reviews.json"), review_template(report))
    print("真实业务对照完成，质量待审核。")
