"""联网搜索及可追溯来源信息。"""

from dataclasses import dataclass
from datetime import date
from urllib.parse import urlparse

from ddgs import DDGS


PREFERRED_MEDIA_DOMAINS = (
    "people.com.cn",
    "xinhuanet.com",
    "cctv.com",
    "cnr.cn",
    "gmw.cn",
    "chinanews.com.cn",
)


@dataclass(frozen=True)
class WebEvidence:
    """一条网络搜索结果和其来源元数据。"""

    label: str
    title: str
    url: str
    site: str
    publisher: str
    published_date: str
    access_date: str
    snippet: str
    applicability: str


def _source_priority(url: str) -> int:
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    if host.endswith(".gov.cn") or host == "gov.cn":
        return 0
    if any(host == domain or host.endswith(f".{domain}") for domain in PREFERRED_MEDIA_DOMAINS):
        return 1
    if host.endswith(".cn"):
        return 2
    return 3


def search_web(query: str, *, news: bool = False, max_results: int = 5) -> list[WebEvidence]:
    """使用中文区域检索，优先官方和中国大陆权威来源。"""
    if not query.strip() or len(query) > 500:
        raise ValueError("搜索关键词不能为空且不能超过 500 个字符。")

    search = DDGS()
    if news:
        news_results = search.news(
            query,
            region="cn-zh",
            safesearch="moderate",
            timelimit="m",
            max_results=max_results * 2,
        )
        official = search.text(
            f"{query} site:gov.cn",
            region="cn-zh",
            safesearch="moderate",
            max_results=max_results,
        )
        raw_results = official + news_results
    else:
        broad = search.text(
            query,
            region="cn-zh",
            safesearch="moderate",
            max_results=max_results * 2,
        )
        official = search.text(
            f"{query} site:gov.cn",
            region="cn-zh",
            safesearch="moderate",
            max_results=max_results,
        )
        raw_results = official + broad

    unique: dict[str, dict[str, str]] = {}
    for item in raw_results:
        url = str(item.get("href") or item.get("url") or "").strip()
        if url.startswith("https://") or url.startswith("http://"):
            unique.setdefault(url, item)

    ranked = sorted(
        unique.values(),
        key=lambda item: _source_priority(str(item.get("href") or item.get("url") or "")),
    )[:max_results]
    accessed = date.today().isoformat()
    results = []
    for index, item in enumerate(ranked, start=1):
        url = str(item.get("href") or item.get("url") or "")
        published = str(
            item.get("date") or item.get("published") or item.get("published_date") or "未注明"
        )
        results.append(WebEvidence(
            label=f"W{index}",
            title=str(item.get("title") or "未提供标题"),
            url=url,
            site=urlparse(url).hostname or "未知网站",
            publisher=str(item.get("source") or "搜索结果未注明"),
            published_date=published,
            access_date=accessed,
            snippet=str(item.get("body") or item.get("snippet") or "")[:1000],
            applicability="搜索摘要未提供适用范围；请核对原文中的地区、时间、产品型号或配置。",
        ))
    return results
