"""本地只记录允许字段的运行观测；记录失败不得影响业务。"""

import json
import re
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from functools import wraps
from threading import Lock

_CURRENT = ContextVar("research_trace", default=None)
_WRITE_LOCK = Lock()


def safe_usage(usage):
    """只接受非负整数计数，忽略供应商附带的任意文本字段。"""
    if not isinstance(usage, dict):
        return None
    result = {}
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        value = usage.get(key)
        result[key] = value if type(value) is int and value >= 0 else None
    details = usage.get("input_token_details") or {}
    cached = details.get("cache_read") if isinstance(details, dict) else None
    result["cache_read_tokens"] = cached if type(cached) is int and cached >= 0 else None
    return result


def _write(trace, settings):
    if not getattr(settings, "telemetry_enabled", True):
        return
    try:
        directory = settings.data_dir.parent / "telemetry"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (datetime.now(UTC).strftime("%Y-%m-%d") + ".jsonl")
        with _WRITE_LOCK, path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(trace, ensure_ascii=False, allow_nan=False) + "\n")
    except Exception:  # noqa: BLE001, S110 - 观测不可中断业务或打印第三方诊断
        pass


@contextmanager
def request_trace(settings, kind):
    existing = _CURRENT.get()
    if existing is not None:
        with span(kind):
            yield existing
        return
    trace = {
        "request_id": uuid.uuid4().hex,
        "kind": kind,
        "created_at": datetime.now(UTC).isoformat(),
        "status": "ok",
        "events": [],
    }
    token = _CURRENT.set(trace)
    started = time.perf_counter()
    try:
        yield trace
    except BaseException:
        trace["status"] = "error"
        raise
    finally:
        trace["total_seconds"] = time.perf_counter() - started
        trace["model_calls"] = sum(e["stage"] == "model" for e in trace["events"])
        trace["api_attempts"] = None
        _CURRENT.reset(token)
        _write(trace, settings)


@contextmanager
def span(stage):
    started = time.perf_counter()
    event = {"stage": stage, "status": "ok"}
    try:
        yield event
    except BaseException as exc:
        event.update(
            status="error", error_type=re.sub(r"[^A-Za-z0-9_]", "", type(exc).__name__)[:80]
        )
        raise
    finally:
        event["seconds"] = time.perf_counter() - started
        trace = _CURRENT.get()
        if trace is not None and len(trace["events"]) < 1000:
            trace["events"].append(event)


def observed_step(stage):
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            with span(stage) as event:
                result = function(*args, **kwargs)
                if (
                    stage == "tool"
                    and isinstance(result, str)
                    and result.startswith(
                        ("工具执行失败", "工具调用已拒绝", "检索未执行", "联网搜索未执行")
                    )
                ):
                    event["status"] = "error"
                return result

        return wrapped

    return decorate


def observed_method(kind):
    def decorate(function):
        @wraps(function)
        def wrapped(self, *args, **kwargs):
            with request_trace(self.settings, kind) as trace:
                if args and args[0] in {
                    "select",
                    "plan",
                    "approval",
                    "local",
                    "coverage",
                    "web_consent",
                    "web_search",
                    "summarize",
                    "review",
                    "report",
                }:
                    trace["node"] = args[0]
                result = function(self, *args, **kwargs)
                status = result.get("task", {}).get("status") if isinstance(result, dict) else None
                if status in {"failed", "cancelled"}:
                    trace["status"] = status
                return result

        return wrapped

    return decorate


def observed_stream(function):
    @wraps(function)
    def wrapped(question, settings, *args, **kwargs):
        with request_trace(settings, "chat") as trace:
            for event in function(question, settings, *args, **kwargs):
                if event.kind == "error":
                    trace["status"] = "error"
                elif event.kind == "web_search_consent":
                    trace["status"] = "awaiting_consent"
                yield event

    return wrapped


class ObservedModel:
    """仅观察返回元数据，不访问或保存messages/content。"""

    def __init__(self, model):
        self.model = model

    def bind_tools(self, tools):
        return ObservedModel(self.model.bind_tools(tools))

    def invoke(self, messages):
        with span("model") as event:
            response = self.model.invoke(messages)
            event["usage"] = safe_usage(getattr(response, "usage_metadata", None))
            return response

    def stream(self, messages):
        with span("model") as event:
            usage = None
            received = False
            for chunk in self.model.stream(messages):
                received = True
                candidate = getattr(chunk, "usage_metadata", None)
                if candidate is not None:
                    usage = safe_usage(candidate)
                yield chunk
            event["usage"] = usage
            if not received:
                event["status"] = "empty"
