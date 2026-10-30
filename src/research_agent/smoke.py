"""真实服务冒烟兼容入口；只有显式执行时才发起请求。"""

from research_agent.evaluation.smoke import main

__all__ = ["main"]

if __name__ == "__main__":
    main()
