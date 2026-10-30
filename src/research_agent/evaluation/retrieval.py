"""只读评估原始资料集合，保存完整候选证据供人工复核。"""

import hashlib
import json
import re
import time
from dataclasses import asdict
from datetime import UTC, datetime

from research_agent.config import PROJECT_ROOT, get_settings


def normalize(text: str) -> str:
    return re.sub(r"\s+", "", text)


def main() -> None:
    from research_agent.retrieval import retrieve

    settings = get_settings()
    questions = json.loads(
        (PROJECT_ROOT / "eval/datasets/questions.json").read_text(encoding="utf-8")
    )
    # 在检索前核对原文版本，防止旧标注对应到修改后的资料。
    for question in questions:
        source = settings.data_dir / question["expected_source"]
        if hashlib.sha256(source.read_bytes()).hexdigest() != question["source_sha256"]:
            raise ValueError(f"原文已变化，请重新核对标注：{question['id']}")
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "scope": "原始集合；使用项目当前相关性阈值；不包含新增集合及生成回答",
        "collection": settings.collection_name,
        "embedding_model": settings.embedding_model,
        "top_k": settings.top_k,
        "relevance_threshold": settings.rag_relevance_threshold,
        "questions_sha256": hashlib.sha256(
            (PROJECT_ROOT / "eval/datasets/questions.json").read_bytes()
        ).hexdigest(),
        "retrieval_code_sha256": hashlib.sha256(
            (PROJECT_ROOT / "src/research_agent/retrieval.py").read_bytes()
        ).hexdigest(),
        "results": [],
    }
    for question in questions:
        started = time.perf_counter()
        evidence = retrieve(question["question"], settings)
        # 所有锚点均出现在同一来源的候选证据中，仅标记为待复核。
        matching = [
            item
            for item in evidence
            if item.source.replace("\\", "/") == question["expected_source"]
        ]
        candidate = any(
            all(
                normalize(anchor["text"]) in normalize(item.text)
                for anchor in question["evidence_anchors"]
            )
            for item in matching
        )
        report["results"].append(
            {
                **question,
                "elapsed_seconds": round(time.perf_counter() - started, 3),
                "anchor_candidate_hit": candidate,
                "retrieved": [asdict(item) for item in evidence],
            }
        )
        print(f"{question['id']} 已检索，返回 {len(evidence)} 条；待人工复核。", flush=True)
    directory = PROJECT_ROOT / "eval/results"
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / (datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + ".json")
    destination.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"结果已保存：{destination}")


if __name__ == "__main__":
    main()
