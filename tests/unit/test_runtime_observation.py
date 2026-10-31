"""观测只保留允许字段；断流、未知用量和文件写入失败不误报。"""

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessageChunk

from research_agent.config import get_settings
from research_agent.observability.runtime import ObservedModel, request_trace, span


def settings_at(tmp_path):
    return replace(get_settings(), data_dir=tmp_path / "documents")


def test_trace_excludes_input_output_and_unknown_usage_fields(tmp_path):
    secret = "此正文不应写入运行日志"

    class Model:
        def invoke(self, messages):
            return SimpleNamespace(
                content=secret,
                usage_metadata={
                    "input_tokens": 10,
                    "output_tokens": 3,
                    "total_tokens": 13,
                    "input_token_details": {"cache_read": 0},
                    "diagnostic": secret,
                },
            )

    with request_trace(settings_at(tmp_path), "chat"):
        with span("retrieval"):
            pass
        assert ObservedModel(Model()).invoke([secret]).content == secret
        with span("save"):
            pass
    raw = next((tmp_path / "telemetry").glob("*.jsonl")).read_text(encoding="utf-8")
    assert secret not in raw
    trace = json.loads(raw)
    assert trace["model_calls"] == 1 and trace["api_attempts"] is None
    assert trace["events"][1]["usage"]["input_tokens"] == 10


def test_failed_observation_write_keeps_business_result(tmp_path):
    (tmp_path / "telemetry").write_text("占用目录位置", encoding="utf-8")
    with request_trace(settings_at(tmp_path), "chat"):
        result = "正常业务结果"
    assert result == "正常业务结果"


def test_stream_preserves_chunks_and_logs_safe_failure(tmp_path):
    class Model:
        def stream(self, _):
            yield AIMessageChunk(content="已输出部分")
            raise TimeoutError("不可记录的远程响应正文")

    with pytest.raises(TimeoutError), request_trace(settings_at(tmp_path), "chat"):
        list(ObservedModel(Model()).stream([]))
    raw = next((tmp_path / "telemetry").glob("*.jsonl")).read_text(encoding="utf-8")
    assert "不可记录" not in raw and "已输出部分" not in raw
    assert json.loads(raw)["status"] == "error"
    assert json.loads(raw)["events"][0]["error_type"] == "TimeoutError"


def test_unknown_usage_and_disabled_telemetry(tmp_path):
    class Model:
        def stream(self, _):
            yield AIMessageChunk(content="正文")

    with request_trace(settings_at(tmp_path), "chat"):
        chunks = list(ObservedModel(Model()).stream([]))
        assert chunks[0].content == "正文"
    trace = json.loads(next((tmp_path / "telemetry").glob("*.jsonl")).read_text(encoding="utf-8"))
    assert trace["events"][0]["usage"] is None
    disabled = tmp_path / "disabled"
    with request_trace(replace(settings_at(disabled), telemetry_enabled=False), "chat"):
        pass
    assert not (disabled / "telemetry").exists()
