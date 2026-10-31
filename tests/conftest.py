"""先隔离凭据再收集测试；网络放行不代表允许使用真实 API 密钥。"""

import ipaddress
import os
import socket

import pytest

# 必须在导入 research_agent 之前执行，autouse fixture 对收集阶段太晚。
os.environ["PYTHON_DOTENV_DISABLED"] = "1"
os.environ["DEEPSEEK_API_KEY"] = "evaluation-test-key"
os.environ["DEEPSEEK_BASE_URL"] = "https://example.invalid"


def pytest_addoption(parser):
    parser.addoption(
        "--allow-external-network",
        action="store_true",
        default=False,
        help="允许显式集成测试联网；仍禁用真实 .env 并使用测试密钥",
    )


def _loopback(host):
    if host is None or host in {"localhost", b"localhost"}:
        return True
    try:
        return ipaddress.ip_address(host.decode() if isinstance(host, bytes) else host).is_loopback
    except ValueError:
        return False


def pytest_configure(config):
    if config.getoption("--allow-external-network"):
        return
    guard = pytest.MonkeyPatch()
    for name in ("connect", "connect_ex"):
        original = getattr(socket.socket, name)

        def guarded(self, address, _original=original):
            if self.family in {socket.AF_INET, socket.AF_INET6} and not _loopback(address[0]):
                raise RuntimeError(
                    "离线测试禁止外部网络；确需联网请显式使用 --allow-external-network。"
                )
            return _original(self, address)

        guard.setattr(socket.socket, name, guarded)
    original_lookup = socket.getaddrinfo

    def lookup(host, *args, **kwargs):
        if not _loopback(host):
            raise RuntimeError("离线测试禁止外部域名解析。")
        return original_lookup(host, *args, **kwargs)

    guard.setattr(socket, "getaddrinfo", lookup)
    original_sendto = socket.socket.sendto

    def sendto(self, data, *args):
        address = args[-1]
        if self.family in {socket.AF_INET, socket.AF_INET6} and not _loopback(address[0]):
            raise RuntimeError("离线测试禁止外部 UDP 发送。")
        return original_sendto(self, data, *args)

    guard.setattr(socket.socket, "sendto", sendto)
    config._external_network_guard = guard


def pytest_unconfigure(config):
    guard = getattr(config, "_external_network_guard", None)
    if guard:
        guard.undo()
