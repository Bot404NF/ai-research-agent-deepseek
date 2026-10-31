"""只读工具调用基线；模拟异常与真实本地服务分开计算。"""

import argparse
import json
import re
import tempfile
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from research_agent.agent import ToolExecutionMiddleware
from research_agent.config import PROJECT_ROOT, get_settings
from research_agent.evaluation.acceptance import digest, distribution
from research_agent.retrieval import Evidence
from research_agent.storage import write_json_atomic


def controlled(settings) -> list[dict]:
    scenarios = [
        (
            "retrieval_ok",
            "search_documents",
            {"query": "测试"},
            [Evidence("S1", "确定的测试证据", "fixture.md")],
            "[S1]",
            "ok",
        ),
        ("retrieval_empty", "search_documents", {"query": "无结果"}, [], "没有检索到", "ok"),
        (
            "retrieval_error",
            "search_documents",
            {"query": "异常"},
            RuntimeError("模拟故障"),
            "工具执行失败（RuntimeError）",
            "error",
        ),
        ("query_empty", "search_documents", {"query": " "}, [], "查询不能为空", "rejected"),
        ("query_long", "search_documents", {"query": "长" * 1001}, [], "不能超过", "rejected"),
        ("unknown_tool", "unknown", {}, [], "已拒绝", "rejected"),
        ("web_disabled", "search_web", {"query": "测试"}, [], "已拒绝", "rejected"),
        ("list_documents", "list_documents", {}, [], "sample.md", "ok"),
    ]
    rows = []
    with tempfile.TemporaryDirectory(prefix="week1-tools-") as directory:
        root = Path(directory)
        (root / "sample.md").write_text("离线测试资料", encoding="utf-8")
        for case_id, tool, arguments, value, expected, outcome in scenarios:
            service = ToolExecutionMiddleware(
                replace(settings, data_dir=root, web_search_enabled=False)
            )
            tick = time.perf_counter()
            with patch(
                "research_agent.agent.retrieve_library",
                **(
                    {"side_effect": value}
                    if isinstance(value, Exception)
                    else {"return_value": value}
                ),
            ) as mock:
                response = service.execute(tool, arguments)
            passed = expected in response
            if outcome == "rejected":
                passed = passed and not mock.called
            if case_id == "retrieval_error":
                passed = passed and service.retrieval_failed
            rows.append(
                {
                    "id": case_id,
                    "tool": tool,
                    "mode": "controlled",
                    "expected_outcome": outcome,
                    "scenario_passed": passed,
                    "backend_called": mock.called,
                    "operation_succeeded": expected in response and outcome == "ok",
                    "response": response,
                    "seconds": time.perf_counter() - tick,
                }
            )
    return rows


def summarize_tools(rows: list[dict]) -> dict:
    live = [r for r in rows if r["mode"] == "live_local"]
    fake = [r for r in rows if r["mode"] == "controlled"]
    # 参数拒绝不是后端执行成功；注入故障即使被正确拦截仍是一次执行失败。
    backend = [r for r in fake if r["backend_called"]]
    return {
        "controlled_scenarios": len(fake),
        "controlled_passed": sum(r["scenario_passed"] for r in fake),
        "controlled_backend_attempts": len(backend),
        "controlled_backend_successes": sum(r["operation_succeeded"] for r in backend),
        "live_tool_attempts": len(live),
        "live_tool_successes": sum(r["operation_succeeded"] for r in live),
        "live_tool_success_rate": sum(r["operation_succeeded"] for r in live) / len(live)
        if live
        else None,
        "live_latency": distribution([r["seconds"] for r in live if r["operation_succeeded"]]),
        "scope": "工具执行/异常保护基线；受控模拟不计真实成功率，检索返回非空不代表证据充分；不衡量LLM工具选择准确率，不执行入库或联网搜索",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="只读工具基线，默认仅离线模拟")
    parser.add_argument(
        "--live-local",
        action="store_true",
        help="额外运行4次现有本地资料库工具，不调用生成或搜索 API",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("输出已存在，请使用新文件。")
    settings = replace(get_settings(), api_key="", web_search_enabled=False)
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "code_sha256": {
            name: digest(PROJECT_ROOT / f"src/research_agent/{name}.py")
            for name in ("agent", "retrieval", "evaluation/tools")
        },
        "scope": "使用实际普通问答的search_documents/retrieve_library，只读合并现有本地集合",
        "results": controlled(settings),
    }
    if args.live_local:
        cases = json.loads((PROJECT_ROOT / "eval/datasets/week1.json").read_text(encoding="utf-8"))[
            "cases"
        ]
        for case in [r for r in cases if r["id"] in {"Q01", "H08", "M05", "N01"}]:
            service = ToolExecutionMiddleware(replace(settings, web_search_enabled=False))
            tick = time.perf_counter()
            response = service.execute("search_documents", {"query": case["question"]})
            row = {
                "id": case["id"],
                "tool": "search_documents",
                "mode": "live_local",
                "operation_succeeded": not service.retrieval_failed
                and not response.startswith("工具执行失败"),
                "evidence_count": len(service.collected_evidence),
                "seconds": time.perf_counter() - tick,
            }
            failure = re.match(r"工具执行失败（([A-Za-z0-9_]+)）", response)
            if failure:
                row["error_type"] = failure.group(1)
            report["results"].append(row)
            write_json_atomic(args.output, report)
    report["summary"] = summarize_tools(report["results"])
    write_json_atomic(args.output, report)
    print(json.dumps(report["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
