"""用合成哨兵验证真实凭据隔离、失败输出和暂存区扫描。"""

import importlib.util
import json
import logging
import os
import pickle
import socket
import subprocess
import sys
import threading
import traceback
from dataclasses import asdict, replace
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

import httpx
import pytest

from research_agent import agent, answer, cli, web_search, web_server, workflow
from research_agent.config import PROJECT_ROOT, get_settings
from research_agent.retrieval import Evidence
from research_agent.security import (
    ExternalServiceError,
    diagnostic_settings,
    public_endpoint,
)

# 拼接生成，避免将看似真实的完整密钥作为仓库固定内容。
SENTINEL = "sk-" + "canary" * 6
SPEC = importlib.util.spec_from_file_location(
    "secret_scanner", PROJECT_ROOT / "scripts/check_secrets.py"
)
scanner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scanner)


@pytest.fixture
def settings(tmp_path):
    return replace(
        get_settings(),
        api_key=SENTINEL,
        data_dir=tmp_path / "documents",
        conversation_dir=tmp_path / "conversations",
        web_search_enabled=False,
    )


def fail(*args, **kwargs):
    raise RuntimeError(SENTINEL)


def test_default_test_environment_and_network_isolation(request):
    assert os.environ["DEEPSEEK_API_KEY"] == "evaluation-test-key"
    assert os.environ["PYTHON_DOTENV_DISABLED"] == "1"
    assert get_settings().api_key == "evaluation-test-key"
    if request.config.getoption("--allow-external-network"):
        return  # 放行测试只检查配置，不实际访问外部主机。
    with socket.socket() as client, pytest.raises(RuntimeError, match="禁止外部"):
        client.connect(("198.51.100.1", 443))
    with pytest.raises(RuntimeError, match="禁止外部"):
        socket.getaddrinfo("example.invalid", 443)
    with (
        socket.socket(type=socket.SOCK_DGRAM) as client,
        pytest.raises(RuntimeError, match="禁止外部"),
    ):
        client.sendto(b"probe", ("198.51.100.1", 53))


@pytest.mark.parametrize("allow_network", [False, True])
def test_collection_isolation_failure_traceback_and_junit(tmp_path, allow_network):
    """子进程使用模拟.env，并故意让一题失败，检查完整回溯及XML。"""
    root = tmp_path / "mini"
    package = root / "src/research_agent"
    tests = root / "tests"
    package.mkdir(parents=True)
    tests.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "config.py").write_text(
        (PROJECT_ROOT / "src/research_agent/config.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tests / "conftest.py").write_text(
        (PROJECT_ROOT / "tests/conftest.py").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (root / ".env").write_text(
        "DEEPSEEK_API_KEY" + chr(61) + SENTINEL + "\nDOTENV_CANARY=unexpected\n", encoding="utf-8"
    )
    (root / "pytest.ini").write_text("[pytest]\npythonpath=src\n", encoding="utf-8")
    (tests / "test_probe.py").write_text(
        """
import os
import socket
from research_agent.config import get_settings

assert os.environ['DEEPSEEK_API_KEY'] == 'evaluation-test-key'
assert 'DOTENV_CANARY' not in os.environ
assert get_settings().api_key == 'evaluation-test-key'

def test_network_option(request):
    allowed = request.config.getoption('--allow-external-network')
    assert (socket.socket.connect.__name__ == 'connect') == allowed

def test_expected_failure():
    settings = get_settings()
    assert False, settings
""",
        encoding="utf-8",
    )
    env = {
        **{
            k: os.environ[k]
            for k in (
                "PATH",
                "SystemRoot",
                "WINDIR",
                "TEMP",
                "TMP",
                "USERPROFILE",
                "LOCALAPPDATA",
                "APPDATA",
            )
            if k in os.environ
        },
        "DEEPSEEK_API_KEY": SENTINEL,
        "PYTHON_DOTENV_DISABLED": "0",
        "PYTHONPATH": str(root / "src"),
        "PYTEST_ADDOPTS": "",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    }
    env.pop("DOTENV_CANARY", None)
    report = root / "result.xml"
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-c",
        str(root / "pytest.ini"),
        str(tests),
        "--showlocals",
        "--tb=long",
        "--junitxml=" + str(report),
        "-o",
        "junit_logging=all",
    ]
    if allow_network:
        command.append("--allow-external-network")
    result = subprocess.run(
        command, cwd=root, env=env, capture_output=True, text=True, timeout=45, check=False
    )
    assert result.returncode == 1
    output = result.stdout + result.stderr + report.read_text(encoding="utf-8")
    assert "1 failed, 1 passed" in output
    assert SENTINEL not in output


def test_diagnostics_are_allowlisted_not_dataclass_serialization(settings):
    configured = replace(
        settings,
        base_url="https://user:" + SENTINEL + "@example.invalid/?key=" + SENTINEL,
        milvus_uri="http://user:" + SENTINEL + "@127.0.0.1:19531/private?token=" + SENTINEL,
    )
    assert SENTINEL not in repr(configured)
    assert SENTINEL in json.dumps(asdict(configured), default=str)  # repr=False不能保护asdict。
    assert SENTINEL not in json.dumps(diagnostic_settings(configured))
    assert public_endpoint(configured.milvus_uri) == "http://127.0.0.1:19531"


def test_model_failure_removes_exception_context_and_pickle_payload(settings, monkeypatch, capsys):
    monkeypatch.setattr(answer, "ChatOpenAI", lambda **kwargs: SimpleNamespace(invoke=fail))
    with pytest.raises(ExternalServiceError) as caught:
        answer.answer_question("测试", [Evidence("S1", "正常证据", "fixture.md")], settings)
    error = caught.value
    assert error.error_type == "RuntimeError"
    assert error.__context__ is None and error.__cause__ is None
    diagnostic = "".join(
        traceback.TracebackException.from_exception(error, capture_locals=True).format()
    )
    assert SENTINEL not in diagnostic
    assert SENTINEL.encode() not in pickle.dumps(error)
    assert SENTINEL not in str(pickle.loads(pickle.dumps(error)))
    assert SENTINEL not in repr(capsys.readouterr())


def test_stream_and_tool_failures_are_safe(settings, monkeypatch):
    monkeypatch.setattr(
        agent, "retrieve_library", lambda *args: [Evidence("S1", "正常证据", "fixture.md")]
    )
    fake = SimpleNamespace(stream=fail)
    fake.bind_tools = lambda tools: fake
    monkeypatch.setattr(agent, "_model", lambda *args: fake)
    with pytest.raises(ExternalServiceError) as caught:
        list(agent.stream_answer_turn("测试", settings, "safe"))
    assert SENTINEL not in "".join(traceback.format_exception(caught.value))
    service = agent.ToolExecutionMiddleware(settings)
    monkeypatch.setattr(agent, "retrieve_library", fail)
    assert "RuntimeError" in service.execute("search_documents", {"query": "测试"})
    monkeypatch.setattr(agent, "index_incoming_file", fail)
    assert SENTINEL not in service.execute("add_file_to_rag", {"relative_path": "sample.md"})


def test_search_http_error_retains_status_not_headers_or_body(settings, monkeypatch):
    request = httpx.Request(
        "POST", "https://example.invalid", headers={"Authorization": "Bearer " + SENTINEL}
    )
    response = httpx.Response(401, request=request, json={"error": SENTINEL})
    failure = httpx.HTTPStatusError(SENTINEL, request=request, response=response)
    client = SimpleNamespace(post=lambda *args, **kwargs: (_ for _ in ()).throw(failure))
    with pytest.raises(RuntimeError, match="HTTP 401") as caught:
        web_search._request(client, "search", {})
    assert caught.value.__context__ is None
    assert SENTINEL not in str(caught.value)
    assert SENTINEL.encode() not in pickle.dumps(caught.value)


def test_cli_unknown_error_has_no_raw_traceback(monkeypatch, capsys):
    monkeypatch.setattr(cli, "get_settings", fail)
    monkeypatch.setattr(sys, "argv", ["research-agent", "ask", "测试"])
    with pytest.raises(SystemExit) as caught:
        cli.main()
    assert caught.value.code == 1
    output = capsys.readouterr()
    assert "RuntimeError" in output.out
    assert SENTINEL not in output.out + output.err


def test_http_json_sse_and_access_logs_never_echo_upstream_secret(settings, monkeypatch, caplog):
    monkeypatch.setattr(web_server, "_knowledge_status", fail)
    monkeypatch.setattr(web_server, "add_file_to_rag", fail)
    monkeypatch.setattr(web_server, "stream_answer_turn", fail)
    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.create_handler(settings))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(method, path, payload=None):
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=10)
        try:
            body = json.dumps(payload).encode() if payload is not None else None
            connection.request(method, path, body, {"Content-Type": "application/json"})
            response = connection.getresponse()
            return response.status, response.read().decode()
        finally:
            connection.close()

    try:
        with caplog.at_level(logging.INFO):
            status, body = request("GET", "/api/knowledge-status?api_key=" + SENTINEL)
            assert status == 500 and "RuntimeError" in body
            assert SENTINEL not in body
            status, body = request("POST", "/api/index", {"path": "sample.md"})
            assert status == 400 and SENTINEL not in body
            status, body = request("POST", "/api/chat", {"question": "测试", "session_id": "safe"})
            assert status == 200 and '"kind": "error"' in body and SENTINEL not in body
            status, body = request("POST", "/api/chat", {})
            assert status == 400 and "问题不能为空" in body
        assert SENTINEL not in caplog.text
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_research_task_json_and_checkpoint_do_not_persist_raw_error(settings, monkeypatch, caplog):
    monkeypatch.setattr(workflow, "_model", lambda *args: SimpleNamespace(invoke=fail))
    service = workflow.ResearchWorkflow(settings)
    task = service.create("安全测试专题")
    service.worker.join(timeout=10)
    assert not service.worker.is_alive()
    result = service.get(task["id"])
    assert result["status"] == "failed"
    assert "RuntimeError" in result["error"]
    assert SENTINEL not in json.dumps(result)
    assert SENTINEL not in caplog.text
    assert service.checkpoint_path.exists()
    for path in service.directory.iterdir():
        if path.is_file():
            assert SENTINEL.encode() not in path.read_bytes()


def test_evaluation_failure_report_is_safe(settings, monkeypatch, tmp_path, capsys):
    from research_agent import retrieval
    from research_agent.evaluation import baseline

    dataset = tmp_path / "dataset.json"
    dataset.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": "x",
                        "stratum": "test",
                        "question": "测试",
                        "should_abstain": False,
                        "gold_groups": [{"source": "fixture.md", "anchors": ["证据"]}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "result.json"
    monkeypatch.setattr(baseline, "get_settings", lambda: settings)
    monkeypatch.setattr(baseline, "validate_dataset", lambda *args: {})
    monkeypatch.setattr(baseline, "generate", fail)
    monkeypatch.setattr(
        retrieval, "retrieve", lambda *args, **kwargs: [Evidence("S1", "正常证据", "fixture.md")]
    )
    monkeypatch.setattr(
        sys, "argv", ["baseline", "--generate", "--dataset", str(dataset), "--output", str(output)]
    )
    baseline.main()
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["summary"]["failed"] == 1
    assert saved["results"][0]["error_type"] == "RuntimeError"
    assert SENTINEL not in output.read_text(encoding="utf-8")
    assert SENTINEL not in repr(capsys.readouterr())


def test_scanner_finds_literals_but_never_prints_them(tmp_path, capsys):
    path = tmp_path / "output.log"
    path.write_text(
        json.dumps({"api_key": SENTINEL}) + "\nAuthorization: Bearer " + SENTINEL + "\n",
        encoding="utf-8",
    )
    assert scanner.scan(tmp_path, paths=["output.log"]) == 1
    output = capsys.readouterr()
    assert "KEY_FORMAT" in output.out and "BEARER_VALUE" in output.out
    assert SENTINEL not in output.out + output.err


def test_scanner_placeholder_real_env_exclusion_and_error_code(tmp_path, capsys):
    (tmp_path / ".env").write_text("DEEPSEEK_API_KEY" + chr(61) + SENTINEL, encoding="utf-8")
    (tmp_path / ".env.example").write_text(
        "DEEPSEEK_API_KEY=your-deepseek-api-key\n", encoding="utf-8"
    )
    assert scanner.scan(tmp_path, paths=[".env", ".env.example"]) == 0
    assert scanner.scan(tmp_path, paths=["missing.log"]) == 2
    assert SENTINEL not in repr(capsys.readouterr())


def test_scanner_uses_staged_blob_not_clean_worktree(tmp_path, capsys):
    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)

    git("init")
    path = tmp_path / "sample.py"
    path.write_text(json.dumps({"api_key": SENTINEL}), encoding="utf-8")
    git("add", "--", "sample.py")
    path.write_text('api_key="test-key"\n', encoding="utf-8")
    assert scanner.scan(tmp_path) == 0
    assert scanner.scan(tmp_path, staged=True) == 1
    path.unlink()
    assert scanner.scan(tmp_path) == 0
    assert scanner.scan(tmp_path, staged=True) == 1
    (tmp_path / ".env").write_text("DEEPSEEK_API_KEY" + chr(61) + SENTINEL, encoding="utf-8")
    git("add", "--", ".env")
    assert scanner.scan(tmp_path, staged=True) == 1
    output = capsys.readouterr()
    assert "FORBIDDEN_SECRET_FILE" in output.out and SENTINEL not in output.out


def test_scanner_command_does_not_echo_invalid_arguments():
    result = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts/check_secrets.py"), "--bad-" + SENTINEL],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert SENTINEL not in result.stdout + result.stderr


@pytest.mark.parametrize(
    "line",
    [
        "PASSWORD" + chr(61) + "not" + "-a-placeholder",
        json.dumps({"pass" + "word": "not" + "-a-placeholder"}),
        "Authorization: Basic " + "dXNlcjpw" + "YXNzd29yZA==",
    ],
    ids=["env-password", "json-password", "basic-header"],
)
def test_scanner_recognizes_other_credential_shapes(line):
    assert scanner.inspect_text(line)
