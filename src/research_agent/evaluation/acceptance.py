"""固定验收题集的只读检索、单用户性能和真实生成记录。"""

import argparse
import hashlib
import json
import math
import platform
import time
from dataclasses import asdict
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

from research_agent.config import PROJECT_ROOT, get_settings
from research_agent.security import protect_external_call
from research_agent.storage import write_json_atomic


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def distribution(values: list[float]) -> dict:
    """P95采用最近秩法；空样本不伪造零延迟。"""
    if not values:
        return {"count": 0, "median_seconds": None, "p95_seconds": None}
    ordered = sorted(values)
    size = len(ordered)
    middle = (ordered[(size - 1) // 2] + ordered[size // 2]) / 2
    return {
        "count": size,
        "median_seconds": middle,
        "p95_seconds": ordered[math.ceil(size * 0.95) - 1],
    }


@protect_external_call
def main() -> None:
    parser = argparse.ArgumentParser(description="固定题集验收；只读数据库，结果写入eval/results")
    parser.add_argument(
        "--generate", action="store_true", help="执行16题真实DeepSeek请求，使用已配置API密钥"
    )
    parser.add_argument("--repeats", type=int, default=3, help="每道可回答题的预热后检索次数")
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("重复次数必须大于零。")
    settings = get_settings()
    if args.generate and not settings.api_key:
        parser.error("真实生成需要已配置的DEEPSEEK_API_KEY，请勿在报告中填写密钥。")
    root = PROJECT_ROOT / "eval"
    questions_path = root / "datasets/holdout_questions.json"
    manifest = json.loads((root / "datasets/holdout_manifest.json").read_text(encoding="utf-8"))
    if digest(questions_path) != manifest["questions_sha256"]:
        raise ValueError("验收题集已经变化，请重新冻结版本。")
    if (
        digest(PROJECT_ROOT / "src/research_agent/retrieval.py")
        != manifest["retrieval_code_sha256"]
    ):
        raise ValueError("检索实现已经变化，请使用新版本重新安排验收。")
    questions = json.loads(questions_path.read_text(encoding="utf-8"))
    for question in questions:
        if (
            not question["should_abstain"]
            and digest(settings.data_dir / question["expected_source"]) != question["source_sha256"]
        ):
            raise ValueError(f"原文版本变化：{question['id']}")
    # 计入检索模块导入、模型初始化和首次数据库查询，区别于预热后单次查询。
    cold_start = time.perf_counter()
    from research_agent.retrieval import retrieve

    first = retrieve(questions[0]["question"], settings)
    cold_seconds = time.perf_counter() - cold_start
    import torch
    from pymilvus import MilvusClient

    client = MilvusClient(uri=settings.milvus_uri, timeout=10)
    try:
        indexes = [
            client.describe_index(settings.collection_name, name)
            for name in client.list_indexes(settings.collection_name)
        ]
        stats = client.get_collection_stats(settings.collection_name)
    finally:
        client.close()
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "scope": "原始集合、单用户、顺序执行；生成使用answer.py证据提示词，非网页完整代理链路",
        "questions_sha256": digest(questions_path),
        "answer_code_sha256": digest(PROJECT_ROOT / "src/research_agent/answer.py"),
        "agent_code_sha256": digest(PROJECT_ROOT / "src/research_agent/agent.py"),
        "generation_thinking": "disabled",
        "retrieval_code_sha256": manifest["retrieval_code_sha256"],
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "packages": {
                name: version(name)
                for name in [
                    "torch",
                    "langchain-milvus",
                    "langchain-openai",
                    "sentence-transformers",
                ]
            },
            "cuda_available": torch.cuda.is_available(),
            "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        },
        "settings": {
            "collection": settings.collection_name,
            "embedding_model": settings.embedding_model,
            "chat_model": settings.chat_model,
            "top_k": settings.top_k,
            "threshold": settings.rag_relevance_threshold,
            "generation_max_tokens": 700,
            "generation_timeout_seconds": 60,
            "generation_max_retries": 0,
        },
        "indexes": indexes,
        "collection_stats": stats,
        "cold_first_query_seconds": cold_seconds,
        "cold_first_query_id": questions[0]["id"],
        "warm_retrieval_samples": [],
        "results": [],
        "generation": [],
    }
    destination = (
        root
        / "results"
        / ("acceptance_" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + ".json")
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(destination, report)
    for question in questions:
        found = (
            first
            if question["id"] == questions[0]["id"]
            else retrieve(question["question"], settings)
        )
        report["results"].append({**question, "retrieved": [asdict(e) for e in found]})
        write_json_atomic(destination, report)
        print(f"{question['id']} 检索完成，{len(found)}条候选。", flush=True)
    for repetition in range(args.repeats):
        for question in questions:
            if question["should_abstain"]:
                continue
            started = time.perf_counter()
            retrieve(question["question"], settings)
            report["warm_retrieval_samples"].append(
                {
                    "id": question["id"],
                    "repetition": repetition + 1,
                    "seconds": time.perf_counter() - started,
                }
            )
        write_json_atomic(destination, report)
        print(f"预热后检索测量 {repetition + 1}/{args.repeats} 完成。", flush=True)
    report["warm_retrieval_summary"] = distribution(
        [s["seconds"] for s in report["warm_retrieval_samples"]]
    )
    write_json_atomic(destination, report)
    if args.generate:
        from langchain_core.messages import HumanMessage, SystemMessage
        from langchain_openai import ChatOpenAI

        from research_agent.answer import (
            CITATION_PATTERN,
            SYSTEM_PROMPT,
            render_context,
            validate_citation_labels,
        )

        model = ChatOpenAI(
            model=settings.chat_model,
            api_key=settings.api_key,
            base_url=settings.base_url,
            temperature=0,
            max_tokens=700,
            timeout=60,
            max_retries=0,
            stream_usage=True,
            extra_body={"thinking": {"type": "disabled"}},
        )
        for row in report["results"]:
            if row["id"] not in manifest["answer_sample"]:
                continue
            # 重新检索，测量同一次请求的检索、首字、完整生成耗时，不使用参考答案作提示。
            started = time.perf_counter()
            item = {"id": row["id"], "status": "running"}
            report["generation"].append(item)
            write_json_atomic(destination, report)
            try:
                evidence = retrieve(row["question"], settings)
                retrieval_seconds = time.perf_counter() - started
                generation_started = time.perf_counter()
                raw = ""
                first_token = None
                finish_reason = None
                response_model = None
                usage = None
                if evidence:
                    messages = [
                        SystemMessage(content=SYSTEM_PROMPT),
                        HumanMessage(
                            content=f"问题：{row['question']}\n\n证据：\n{render_context(evidence)}"
                        ),
                    ]
                    for chunk in model.stream(messages):
                        if isinstance(chunk.content, str) and chunk.content:
                            if first_token is None:
                                first_token = time.perf_counter() - generation_started
                            raw += chunk.content
                        response_model = chunk.response_metadata.get("model_name", response_model)
                        finish_reason = chunk.response_metadata.get("finish_reason", finish_reason)
                        usage = chunk.usage_metadata or usage
                else:
                    raw = "提供的资料没有足够信息来回答这个问题。"
                cleaned, invalid = validate_citation_labels(raw, {e.label for e in evidence})
                generation_seconds = time.perf_counter() - generation_started
                item.update(
                    status="ok",
                    question=row["question"],
                    raw_answer=raw,
                    answer=cleaned,
                    retrieved=[asdict(e) for e in evidence],
                    retrieval_seconds=retrieval_seconds,
                    first_content_seconds=first_token,
                    generation_seconds=generation_seconds,
                    end_to_end_seconds=time.perf_counter() - started,
                    used_api=bool(evidence),
                    invalid_labels=list(invalid),
                    cited_labels=sorted(set(CITATION_PATTERN.findall(raw))),
                    finish_reason=finish_reason,
                    response_model=response_model,
                    usage=usage,
                )
            except Exception as exc:  # noqa: BLE001 - 批量验收保留失败样本并继续记录后续题目
                # 只记录异常类型，避免连接诊断中意外保存密钥或请求头。
                item.update(
                    status="error",
                    error_type=type(exc).__name__,
                    elapsed_seconds=time.perf_counter() - started,
                )
            write_json_atomic(destination, report)
            print(f"{row['id']} 生成状态：{item['status']}。", flush=True)
        completed = [
            item for item in report["generation"] if item["status"] == "ok" and item["used_api"]
        ]
        report["generation_summary"] = {
            "successful_api_requests": len(completed),
            "failed_requests": sum(item["status"] == "error" for item in report["generation"]),
            "first_content": distribution(
                [
                    item["first_content_seconds"]
                    for item in completed
                    if item["first_content_seconds"] is not None
                ]
            ),
            "generation": distribution([item["generation_seconds"] for item in completed]),
            "end_to_end": distribution([item["end_to_end_seconds"] for item in completed]),
        }
        write_json_atomic(destination, report)
    print(f"验收原始记录：{destination}")


if __name__ == "__main__":
    main()
