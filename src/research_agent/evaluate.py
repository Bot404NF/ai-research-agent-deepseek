"""开发集评测兼容入口；实现已迁至 evaluation.retrieval。"""

from research_agent.evaluation.retrieval import main, normalize

__all__ = ["main", "normalize"]

if __name__ == "__main__":
    main()
