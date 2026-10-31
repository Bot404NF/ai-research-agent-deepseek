"""复用冻结的第一周题集，附加一道原文核对的新编开发题。"""

import hashlib
import json
from pathlib import Path


def build(root: Path) -> dict:
    original = root / "week1.json"
    dataset = json.loads(original.read_text(encoding="utf-8"))
    data = root.parents[1] / "data/documents"
    groups = []
    for source, anchor in [
        ("上海地方志资料集/安亭志/安亭志.md", "一九五九年十月，建安亭人民公社"),
        ("上海地方志资料集/上海 南翔镇志/上海 南翔镇志.md", "赋役、户口、保甲、乡约概不载"),
    ]:
        path = data / source
        if anchor not in path.read_text(encoding="utf-8"):
            raise ValueError("新编题锚点不在原文中。")
        groups.append(
            {
                "source": source,
                "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "anchors": [anchor],
            }
        )
    dataset["cases"].append(
        {
            "id": "W201",
            "stratum": "authored_probe",
            "question": "请分别回答并逐项引用证据：①《安亭志》前言记载，安亭人民公社何时建立？"
            " ②《南翔镇志》前言介绍的编纂体例中，哪四类事项概不记载？",
            "reference_answer": "①1959年10月。②赋役、户口、保甲、乡约。",
            "should_abstain": False,
            "gold_groups": groups,
            "provenance": {
                "split": "authored_development",
                "author": "Codex",
                "date": "2026-10-31",
            },
            "annotation_status": "本轮核对Markdown原文；未校勘扫描原件；非独立真人验收",
        }
    )
    dataset["purpose"] = "第二周：56道公开回归题与1道新编开发题；非独立验收集"
    dataset["parent_dataset_sha256"] = hashlib.sha256(original.read_bytes()).hexdigest()
    return dataset


if __name__ == "__main__":
    directory = Path(__file__).resolve().parent
    output = directory / "week2.json"
    if output.exists():
        raise ValueError("第二周题集已存在，不覆盖冻结版本。")
    output.write_text(
        json.dumps(build(directory), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
