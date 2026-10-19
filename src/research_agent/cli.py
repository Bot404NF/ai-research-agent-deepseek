import argparse
import uuid

from research_agent.agent import stream_answer_turn
from research_agent.answer import answer_versioned_question
from research_agent.config import get_settings
from research_agent.ingest import build_index
from research_agent.retrieval import retrieve_versions


def main() -> None:
    parser = argparse.ArgumentParser(description="基于 Milvus 的本地文档 AI 研究助手")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("ingest", help="根据本地资料重建向量索引")
    ask_parser = subparsers.add_parser("ask", help="基于已索引的资料提问")
    ask_parser.add_argument("question", help="要提出的问题")
    chat_parser = subparsers.add_parser("chat", help="进入可恢复的多轮问答会话")
    chat_parser.add_argument("--session", help="恢复指定会话；省略时创建新会话")
    web_parser = subparsers.add_parser("web", help="启动本地网页演示")
    web_parser.add_argument("--port", type=int, default=8765, help="本地网页端口，默认 8765")
    args = parser.parse_args()
    settings = get_settings()
    if args.command == "ingest":
        if not settings.api_key:
            parser.error("请先在 .env 中设置 DEEPSEEK_API_KEY，再导入资料。")
        count = build_index(settings)
        print(f"已将 {count} 个文本块写入 Milvus：{settings.milvus_uri}")
    elif args.command == "ask":
        if not settings.api_key:
            parser.error("请先在 .env 中设置 DEEPSEEK_API_KEY，再提问。")
        new_evidence, old_evidence = retrieve_versions(args.question, settings)
        print(answer_versioned_question(args.question, new_evidence, old_evidence, settings))
        if new_evidence:
            print("\n新增资料来源：")
            for item in new_evidence:
                print(f"[{item.label}] {item.display_source}")
        if old_evidence:
            print("\n旧资料来源：")
            for item in old_evidence:
                print(f"[{item.label}] {item.display_source}")
    elif args.command == "chat":
        if not settings.api_key:
            parser.error("请先在 .env 中设置 DEEPSEEK_API_KEY，再开始会话。")
        session_id = args.session or uuid.uuid4().hex[:12]
        print(f"会话 ID：{session_id}（输入 :q 结束；再次运行 --session {session_id} 可继续）")
        while True:
            try:
                question = input("你：").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n会话已结束。")
                break
            if not question:
                continue
            if question.lower() in {":q", "quit", "exit"}:
                print("会话已结束。")
                break
            try:
                allow_web_search = False
                decline_web_search = False
                while True:
                    print("助手：", end="", flush=True)
                    consent_requested = False
                    for event in stream_answer_turn(
                        question,
                        settings,
                        session_id,
                        allow_web_search=allow_web_search,
                        decline_web_search=decline_web_search,
                    ):
                        if event.kind == "token":
                            print(event.text, end="", flush=True)
                        elif event.kind == "replace":
                            print("\n\n引用校验后的回答：\n" + event.text)
                        elif event.kind == "web_search_consent":
                            print(f"\n{event.text}")
                            consent = input("输入 Y 同意联网搜索，其他键表示暂不联网：").strip().lower()
                            allow_web_search = consent in {"y", "yes", "是"}
                            decline_web_search = not allow_web_search
                            consent_requested = True
                            break
                        elif event.kind == "error":
                            print(f"\n{event.text}")
                        elif event.kind == "sources":
                            if event.new_sources:
                                print("\n\n新增资料来源：")
                                for source in event.new_sources:
                                    print(f"- {source}")
                            if event.old_sources:
                                print("\n旧资料来源：")
                                for source in event.old_sources:
                                    print(f"- {source}")
                            if event.web_sources:
                                print("\n联网来源：")
                                for source in event.web_sources:
                                    print(f"- {source}")
                        elif event.kind == "suggestions":
                            print("\n\n可继续追问：")
                            for suggestion in event.suggestions:
                                print(f"- {suggestion}")
                    print()
                    if not consent_requested:
                        break
            except Exception as exc:
                print(f"本轮处理失败（{type(exc).__name__}）。会话记录仍保留，可稍后重试。")
    else:
        if not settings.api_key:
            parser.error("请先在 .env 中设置 DEEPSEEK_API_KEY，再启动网页演示。")
        from research_agent.web_server import serve

        serve(settings, port=args.port)


if __name__ == "__main__":
    main()
