"""复用交接的HTTP/CLI/研究冒烟，但全部模型调用纳入第四周预算。"""

import argparse
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from verify_business import smoke, smoke_checks

from research_agent import agent, answer, workflow
from research_agent.config import PROJECT_ROOT, get_settings
from research_agent.evaluation.performance import Budget, model_factory
from research_agent.storage import write_json_atomic


def main():
    parser = argparse.ArgumentParser(description="第四周隔离业务冒烟，会计入10元总预算")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("结果已存在，拒绝覆盖。")
    settings = replace(
        get_settings(), grounded_answers=True, followup_mode="local", web_search_enabled=False
    )
    budget = Budget(PROJECT_ROOT / "eval/results/week4_budget_20261031.json")
    calls = []
    report = {"status": "running", "calls": calls}

    def save(result):
        report["smoke"] = result
        write_json_atomic(args.output, report)

    with ExitStack() as stack:
        stack.enter_context(
            patch.object(agent, "_model", lambda s: model_factory(s, budget, calls))
        )
        stack.enter_context(
            patch.object(workflow, "_model", lambda s: model_factory(s, budget, calls))
        )
        stack.enter_context(
            patch.object(answer, "ChatOpenAI", lambda **kw: model_factory(settings, budget, calls))
        )
        stack.enter_context(patch("research_agent.cli.get_settings", lambda: settings))
        result = smoke(settings, PROJECT_ROOT / "eval/runtime" / args.output.stem, save)
    report["checks"] = smoke_checks(result)
    report["status"] = "completed" if all(report["checks"].values()) else "completed_with_errors"
    save(result)
    print(report["status"], report["checks"])


if __name__ == "__main__":
    main()
