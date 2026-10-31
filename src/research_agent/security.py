"""安全诊断与外部调用异常边界；不打印原始配置、响应或异常正文。"""

from functools import wraps
from urllib.parse import urlsplit


class PublicInputError(ValueError):
    """应用自身构造、允许向用户展示的参数校验提示。"""


def error_type(exc: Exception) -> str:
    return exc.error_type if isinstance(exc, ExternalServiceError) else type(exc).__name__


def error_details(exc: Exception) -> dict:
    details = {"error_type": error_type(exc)}
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    if type(status) is int and 100 <= status <= 599:
        details["status_code"] = status
    return details


class ExternalServiceError(RuntimeError):
    """只携带安全元数据；不持有原异常或响应对象。"""

    def __init__(self, details: dict):
        self.error_type = details["error_type"]
        self.status_code = details.get("status_code")
        suffix = f"，HTTP {self.status_code}" if self.status_code is not None else ""
        super().__init__(f"外部调用未完成（{self.error_type}{suffix}），请检查服务配置后重试。")

    def __reduce__(self):
        details = {"error_type": self.error_type}
        if self.status_code is not None:
            details["status_code"] = self.status_code
        return type(self), (details,)


def protect_external_call(function):
    """异常处理块退出后再抛出新异常，移除原始异常上下文引用。"""

    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - 外部调用边界不得透传第三方诊断正文
            details = error_details(exc)
        raise ExternalServiceError(details) from None

    return wrapped


def protect_external_stream(function):
    """生成器需在迭代期间捕获异常，不能只保护生成器的创建。"""

    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            yield from function(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            details = error_details(exc)
        else:
            return
        raise ExternalServiceError(details) from None

    return wrapped


def validation_message(exc: Exception) -> str:
    if isinstance(exc, PublicInputError):
        return str(exc)
    return f"操作未完成（{error_type(exc)}），请检查输入或服务状态后重试。"


def diagnostic_settings(settings) -> dict:
    """允许列表中不包含凭据、连接字符串、文件路径或请求头。"""
    return {
        "model": settings.chat_model,
        "embedding_model": settings.embedding_model,
        "top_k": settings.top_k,
        "relevance_threshold": settings.rag_relevance_threshold,
        "web_search_enabled": settings.web_search_enabled,
    }


def public_endpoint(uri: str) -> str:
    """连接状态只展示协议、主机与端口，去掉用户信息、路径和查询参数。"""
    try:
        value = urlsplit(uri)
        host = value.hostname
        if value.scheme not in {"http", "https"} or not host:
            return "本地资料服务"
        host = f"[{host}]" if ":" in host else host
        port = f":{value.port}" if value.port is not None else ""
        return f"{value.scheme}://{host}{port}"
    except ValueError:
        return "本地资料服务"
