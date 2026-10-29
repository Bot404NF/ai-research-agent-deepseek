"""无外部服务的浏览器验收入口；演示数据与真实研究任务完全隔离。"""

import argparse
import json
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from research_agent import web_server, workflow
from research_agent.config import Settings
from research_agent.retrieval import Evidence
from research_agent.web_search import WebEvidence


def main():
    parser = argparse.ArgumentParser(description="启动使用模拟证据的工作流验收网页")
    parser.add_argument("--port", type=int, default=8877)
    parser.add_argument("--directory", type=Path, default=Path("work/workflow-demo"))
    parser.add_argument("--delay", type=float, default=2)
    args = parser.parse_args()
    settings = Settings(
        "demo-placeholder",
        "offline-demo",
        "unused",
        args.directory / "documents",
        "unused",
        "unused",
        800,
        120,
        4,
        conversation_dir=args.directory / "conversations",
    )
    service = workflow.ResearchWorkflow(settings)

    def generate(instruction, context):
        time.sleep(args.delay)
        if "判断本地证据" in instruction:
            return json.dumps({"sufficient": False, "gaps": "仅有示例资料，需要补充证据。"})
        if "只输出 JSON" in instruction:
            return json.dumps(
                {
                    "scope": "仅使用模拟证据演示，不代表真实研究结论。",
                    "expected_output": "展示分题取证、来源冲突与报告的模拟专题研究。",
                    "questions": [
                        {"question": title, "search_query": title, "needs_current": i == 2}
                        for i, title in enumerate(
                            ("主要应用场景有哪些？", "实施需要哪些条件？", "当前有哪些局限？")
                        )
                    ],
                },
                ensure_ascii=False,
            )
        if "阶段结论" in instruction:
            label = context["evidence"][0]["label"]
            return f"### 阶段结论\n演示资料描述了应用场景及条件[{label}]。\n\n### 待核实\n示例版本存在差异，需要核对配置与日期。"
        if "检查各子问题" in instruction:
            return "## 证据缺口\n演示资料不能支持真实市场结论。\n\n## 来源冲突\n模拟新版为20[S1]，旧版为10[S2]，不应合并。\n\n## 适用范围与时效局限\n所有数据仅用于功能验收。"
        return "## 主要结论\n模拟资料展示了任务取证流程[S1]。\n\n## 分题分析\n按应用场景、实施条件和局限三个维度组织成果。\n\n## 分歧与局限\n新版20[S1]与旧版10[S2]需要分别记录。所有证据均为模拟，不构成真实研究结论。"

    def retrieve(*_):
        time.sleep(args.delay)
        return [
            Evidence("S1", "【模拟证据】新版示例值20，适用于演示配置。", "模拟新版.md", 1, 0.9),
            Evidence("S2", "【模拟证据】旧版示例值10，仅适用于旧配置。", "模拟旧版.md", 2, 0.8),
        ]

    def search(*_, **__):
        time.sleep(args.delay)
        return [
            WebEvidence(
                "W1",
                "模拟网络公告",
                "https://example.invalid/demo",
                "example.invalid",
                "模拟机构",
                "未注明",
                "2026-10-29",
                "【模拟证据】这是测试搜索结果，未访问网络。",
                "仅功能验收",
            )
        ]

    service._generate = generate
    with (
        patch.object(workflow, "retrieve_library", retrieve),
        patch.object(workflow, "search_web", search),
        patch.object(web_server, "list_databases", lambda _: []),
        patch.object(web_server, "database_details", lambda _: []),
        patch.object(
            web_server,
            "_knowledge_status",
            lambda _: {
                "connected": False,
                "collections": {"new": False, "old": False},
            },
        ),
    ):
        server = ThreadingHTTPServer(
            ("127.0.0.1", args.port), web_server.create_handler(settings, service)
        )
        print(
            f"模拟验收网页：http://127.0.0.1:{args.port}（不调用模型、Milvus 或网络搜索）",
            flush=True,
        )
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


if __name__ == "__main__":
    main()
