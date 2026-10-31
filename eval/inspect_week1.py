"""只读打印逐题审核材料；未命中题显示全部候选，命中题显示全部被引证据。"""

import argparse
import json
import re

from research_agent.config import PROJECT_ROOT


def main() -> None:
    parser = argparse.ArgumentParser(description="打印指定题号的原始审核材料，不调用模型")
    parser.add_argument("ids", nargs="+")
    args = parser.parse_args()
    cases = {
        x["id"]: x
        for x in json.loads(
            (PROJECT_ROOT / "eval/datasets/week1.json").read_text(encoding="utf-8")
        )["cases"]
    }
    results = json.loads(
        (PROJECT_ROOT / "eval/results/week1_live_20261031.json").read_text(encoding="utf-8")
    )["results"]
    for row in results:
        if row["id"] not in args.ids:
            continue
        case = cases[row["id"]]
        print(
            f"\n=== {row['id']} {case['question']}\n参考：{case['reference_answer']}\n回答：{row['answer']}"
        )
        print("标注：", case["gold_groups"])
        cited = set(re.findall(r"\[([SWNO]\d+)\]", row["answer"]))
        for evidence in row["retrieved"]:
            show = (
                not cited
                or evidence["label"] in cited
                or row["metrics"]["anchor_hit_at_4"] is not True
            )
            print(
                evidence["label"],
                evidence["source"],
                evidence["text"] if show else "未引用候选；自动锚点命中题，完整内容保存在原始 JSON",
            )


if __name__ == "__main__":
    main()
