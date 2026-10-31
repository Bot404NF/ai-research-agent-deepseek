"""联网搜索及可追溯来源信息。"""

from dataclasses import dataclass
from datetime import date, timedelta
from urllib.parse import urlparse

import httpx

from research_agent.config import Settings, get_settings

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


def _request(client: httpx.Client, endpoint: str, payload: dict) -> dict:
    """不透传响应正文，避免服务端错误中的敏感信息进入对话。"""
    try:
        response = client.post(endpoint, json=payload)
        response.raise_for_status()
        result = response.json()
    except httpx.HTTPStatusError as exc:
        failure = f"DeepSeek 联网接口请求失败（HTTP {exc.response.status_code}）。"
    except (httpx.RequestError, ValueError):
        failure = "DeepSeek 联网接口连接失败或返回格式无效。"
    else:
        if not isinstance(result, dict):
            raise RuntimeError("DeepSeek 联网接口返回格式无效。")  # noqa: TRY004 - 保留服务响应格式错误契约
        return result
    raise RuntimeError(failure) from None


def search_web(
    query: str,
    *,
    news: bool = False,
    max_results: int = 5,
    settings: Settings | None = None,
) -> list[WebEvidence]:
    """通过 DeepSeek 搜索；缺少正文时按需抓取，优先中国大陆来源。"""
    suffix = " lang:zh"
    if news:
        suffix += f" after:{(date.today() - timedelta(days=30)).isoformat()}"
    if not query.strip() or len(query.strip()) + len(suffix) > 128:
        raise ValueError("搜索关键词不能为空，且加上语言和日期条件后不能超过 128 个字符。")
    if not 1 <= max_results <= 10:
        raise ValueError("搜索结果条数必须在 1 到 10 之间。")
    settings = settings or get_settings()
    if not settings.api_key.strip():
        raise ValueError("联网搜索需要配置 DEEPSEEK_API_KEY。")
    base = settings.base_url.rstrip("/")
    base = base.removesuffix("/v1")
    with httpx.Client(
        base_url=base + "/",
        timeout=30,
        headers={"Authorization": f"Bearer {settings.api_key}"},
    ) as client:
        payload = _request(
            client,
            "v1/tools/search",
            {
                "query": query.strip() + suffix,
                "count": min(max_results * 2, 50),
                "need_content": True,
            },
        )
        raw_results = payload.get("results")
        if not isinstance(raw_results, list) or any(not isinstance(x, dict) for x in raw_results):
            raise RuntimeError("DeepSeek 搜索结果格式无效。")
        unique = {}
        for item in raw_results:
            url = str(item.get("url") or "").strip()
            parsed = urlparse(url)
            if (
                parsed.scheme in {"http", "https"}
                and parsed.hostname
                and not parsed.username
                and not parsed.password
            ):
                unique.setdefault(url, item)
        ranked = sorted(unique.items(), key=lambda pair: _source_priority(pair[0]))[:max_results]
        results = []
        for index, (url, item) in enumerate(ranked, start=1):
            content = str(item.get("content") or "").strip()
            fetch_note = ""
            if not content:
                try:
                    page = _request(client, "v1/tools/fetch", {"url": url})
                    content = str(page.get("content") or "").strip()
                except RuntimeError:
                    fetch_note = "正文抓取失败；"
            if content:
                text = "网页正文（截取）：\n" + content[:6000]
            else:
                text = (
                    fetch_note
                    + "仅搜索摘要（未取得正文）："
                    + str(item.get("summary") or item.get("snippet") or "")[:1000]
                )
            results.append(
                WebEvidence(
                    label=f"W{index}",
                    title=str(item.get("title") or "未提供标题"),
                    url=url,
                    site=urlparse(url).hostname or "未知网站",
                    publisher=str(item.get("publisher") or "搜索结果未注明发布机构"),
                    published_date=str(item.get("date") or "未注明"),
                    access_date=date.today().isoformat(),
                    snippet=text,
                    applicability="适用范围未自动核验；请核对原文中的地区、时间、产品型号或配置。",
                )
            )
    return results
