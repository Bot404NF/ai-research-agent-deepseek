"""固定题集验收兼容入口；保留原有命令和统计函数。"""

from research_agent.evaluation.acceptance import digest, distribution, main

__all__ = ["digest", "distribution", "main"]

if __name__ == "__main__":
    main()
