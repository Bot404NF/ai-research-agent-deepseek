"""不调用模型或网络的 LangGraph 入门练习，演示跨进程保存与人工恢复。"""

import argparse
import json
from pathlib import Path
from typing import TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt


class LessonState(TypedDict):
    """State 是节点之间传递的数据，不是模型的隐含思考过程。"""

    topic: str
    outline: list[str]
    approved: bool
    result: str


def plan(state: LessonState) -> dict:
    # Node 可以是普通 Python 函数，不必调用 LLM；只返回需要更新的字段。
    return {"outline": [f"研究 {state['topic']} 的用途", "核对适用条件", "整理局限"]}


def approve(state: LessonState) -> dict:
    # 本函数恢复时从头运行，因此这里不进行付费调用或不可重复的写入。
    decision = interrupt({"question": "是否确认这个计划？", "outline": state["outline"]})
    if not isinstance(decision, bool):
        raise TypeError("本练习只接受布尔确认值。")
    return {"approved": decision}


def finish(state: LessonState) -> dict:
    return {"result": f"已确认：{state['topic']}；这里可以接入研究工具。"}


def reject(state: LessonState) -> dict:
    return {"result": "用户拒绝了计划，未执行后续研究。"}


def build_graph(checkpointer=None):
    builder = StateGraph(LessonState)
    builder.add_node("plan", plan)
    builder.add_node("approve", approve)
    builder.add_node("finish", finish)
    builder.add_node("reject", reject)
    builder.add_edge(START, "plan")
    builder.add_edge("plan", "approve")
    # 条件边由程序根据 State 选择下一步，不是每次都询问模型。
    builder.add_conditional_edges(
        "approve",
        lambda state: "finish" if state["approved"] else "reject",
        ["finish", "reject"],
    )
    builder.add_edge("finish", END)
    builder.add_edge("reject", END)
    return builder.compile(checkpointer=checkpointer)


def main():
    parser = argparse.ArgumentParser(description="LangGraph 本地学习练习，不调用任何 API")
    parser.add_argument("action", choices=("start", "approve", "reject", "inspect", "diagram"))
    parser.add_argument("--thread", default="lesson1", help="学习任务标识，跨进程恢复时保持相同")
    parser.add_argument("--topic", default="专题研究助手")
    parser.add_argument("--directory", type=Path, default=Path("work/langgraph-learning"))
    args = parser.parse_args()
    if args.action == "diagram":
        print(build_graph().get_graph().draw_mermaid())
        return
    args.directory.mkdir(parents=True, exist_ok=True)
    # SQLite 是本地文件，不需要部署独立数据库服务；不使用内存型 saver。
    with SqliteSaver.from_conn_string(str(args.directory / "lesson.sqlite")) as saver:
        graph = build_graph(saver)
        config = {"configurable": {"thread_id": args.thread}}
        snapshot = graph.get_state(config)
        if args.action == "start":
            if snapshot.values:
                parser.error("该 thread 已存在，请改用新的 --thread，不会覆盖旧练习。")
            graph.invoke(
                {"topic": args.topic, "outline": [], "approved": False, "result": ""},
                config,
                durability="sync",
            )
        elif args.action in {"approve", "reject"}:
            if not any(item.interrupts for item in snapshot.tasks):
                parser.error("该 thread 当前没有等待确认，请先执行 start。")
            graph.invoke(Command(resume=args.action == "approve"), config, durability="sync")
        snapshot = graph.get_state(config)
        print(
            json.dumps(
                {
                    "thread_id": args.thread,
                    "state": snapshot.values,
                    "next": snapshot.next,
                    "interrupts": [
                        pause.value for item in snapshot.tasks for pause in item.interrupts
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
