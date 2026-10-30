"""开发集检索入口；需要现有 Milvus 与本地模型，不调用生成 API。"""

from research_agent.evaluation.retrieval import main

if __name__ == "__main__":
    main()
