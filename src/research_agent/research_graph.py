"""研究图的拓扑定义：先看 State，再沿 Node 和 Edge 阅读业务流程。"""

from functools import partial
from typing import TYPE_CHECKING, TypedDict

from langgraph.graph import END, START, StateGraph

if TYPE_CHECKING:
    from research_agent.workflow import ResearchWorkflow


class ResearchState(TypedDict):
    """一次研究的共享状态；节点返回更新，LangGraph 在节点边界保存它。"""

    task: dict
    index: int
    needs_web: bool
    route: str


def build_research_graph(service: "ResearchWorkflow", checkpointer):
    """业务工具仍由 service 提供，执行顺序由图管理，不再运行手写步骤循环。"""
    builder = StateGraph(ResearchState)
    nodes = (
        "plan",
        "approval",
        "select",
        "local",
        "coverage",
        "web_consent",
        "web_search",
        "summarize",
        "review",
        "report",
    )
    for name in nodes:
        builder.add_node(name, partial(service._graph_step, name))

    def entry(state: ResearchState) -> str:
        task = state["task"]
        if task["confirmed"]:
            return "select"  # 兼容已确认但尚无图 checkpoint 的历史 JSON 任务。
        return "approval" if task["plan"] else "plan"

    builder.add_conditional_edges(START, entry, ["plan", "approval", "select"])

    def after(target: str):
        def route(state: ResearchState) -> str:
            return END if state["task"]["status"] == "cancelled" else target

        return route

    for source, target in (
        ("plan", "approval"),
        ("approval", "select"),
        ("local", "coverage"),
        ("coverage", "web_consent"),
        ("web_consent", "web_search"),
        ("web_search", "summarize"),
        ("summarize", "select"),
        ("review", "select"),
        ("report", "select"),
    ):
        builder.add_conditional_edges(source, after(target), [target, END])
    builder.add_conditional_edges(
        "select",
        lambda state: state["route"],
        ["local", "review", "report", END],
    )
    # compile 把拓扑变成可执行图；checkpointer 才让 interrupt 跨进程重启后继续。
    return builder.compile(checkpointer=checkpointer, name="topic_research")
