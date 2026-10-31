"""无依赖凭据扫描：只输出位置和规则，不输出匹配值或原始行。"""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", "data", ".ssh", ".aws"}
SKIP_SUFFIXES = {".sqlite", ".sqlite3", ".db", ".wal", ".shm", ".pyc", ".png", ".jpg", ".pdf"}
PLACEHOLDERS = {
    "",
    "test",
    "test-key",
    "test_key",
    "evaluation-test",
    "evaluation-test-key",
    "evaluation-secret-sentinel",
    "your-deepseek-api-key",
    "your-api-key",
    "YOUR_API_KEY",
    "[REDACTED]",
    "***",
    "<redacted>",
    "minioadmin",  # 现有 Compose 的公开示例值；弱默认口令不属于真实密钥扫描。
}
KEY = re.compile(
    r"\b(?:sk-(?:proj-)?[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[A-Z0-9]{16})\b"
)
ASSIGNMENT = re.compile(
    r"""(?ix)["']?(?:api[_-]?key|deepseek_api_key|access[_-]?token|secret[_-]?key|password|client[_-]?secret)["']?\s*[:=]\s*["']([^"'\r\n]*)["']"""
)
ENV_ASSIGNMENT = re.compile(
    r"^\s*(?:export\s+)?(?:[A-Z][A-Z0-9_]*_)?(?:API_KEY|KEY|TOKEN|PASSWORD|SECRET|ACCESS_TOKEN|SECRET_KEY)(?:_[A-Z0-9_]+)?\s*[:=]\s*([A-Za-z0-9._~+/-]+)[ \t]*(?:\#.*)?$"
)
BEARER = re.compile(r"(?i)\bbearer[ \t]+([A-Za-z0-9._~+/-]{8,}=*)")
AUTHORIZATION = re.compile(
    r"""(?i)["']?Authorization["']?\s*[:=]\s*["']?Basic[ \t]+([A-Za-z0-9+/]{8,}=*)"""
)


def placeholder(value: str) -> bool:
    return value in PLACEHOLDERS or value.startswith(("${", "$env:"))


def inspect_text(text: str) -> list[tuple[int, str]]:
    findings = []
    for number, line in enumerate(text.splitlines(), 1):
        if KEY.search(line):
            findings.append((number, "KEY_FORMAT"))
        for rule, pattern in (
            ("CREDENTIAL_ASSIGNMENT", ASSIGNMENT),
            ("ENV_CREDENTIAL", ENV_ASSIGNMENT),
            ("BEARER_VALUE", BEARER),
            ("AUTHORIZATION_VALUE", AUTHORIZATION),
        ):
            if any(not placeholder(m.group(1).strip("\"'")) for m in pattern.finditer(line)):
                findings.append((number, rule))
    return findings


def excluded(relative: Path) -> bool:
    return (
        any(part in SKIP_DIRS or part.startswith(".pytest") for part in relative.parts)
        or (relative.name.startswith(".env") and relative.name != ".env.example")
        or relative.suffix.lower() in SKIP_SUFFIXES
        or relative.name.endswith(("-wal", "-shm"))
    )


def git(root: Path, *arguments: str) -> bytes:
    result = subprocess.run(["git", "-C", str(root), *arguments], capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError("Git 扫描输入不可用。")
    return result.stdout


def candidates(root: Path, staged: bool, paths: list[str] | None):
    if paths is None:
        names = (
            git(root, "diff", "--cached", "--name-only", "--diff-filter=ACMRT", "-z")
            if staged
            else git(root, "ls-files", "-z")
        )
        for name in names.decode("utf-8").split("\0"):
            if name:
                yield Path(name)
        return
    for name in paths:
        path = Path(name)
        path = path if path.is_absolute() else root / path
        if not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("扫描目标必须位于仓库目录内。")
        if not path.exists():
            raise FileNotFoundError("指定扫描目标不存在。")
        if path.is_symlink():
            raise ValueError("不扫描符号链接。")
        relative = path.relative_to(root)
        if excluded(relative):
            continue
        if path.is_file():
            yield relative
        else:
            for directory, folders, files in os.walk(path, followlinks=False):
                parent = Path(directory)
                folders[:] = [
                    p
                    for p in folders
                    if not (parent / p).is_symlink()
                    and not excluded((parent / p).relative_to(root))
                ]
                for filename in files:
                    yield (parent / filename).relative_to(root)


def scan(root: Path, *, staged=False, paths=None) -> int:
    found, failed, count = False, False, 0
    try:
        for relative in sorted(set(candidates(root, staged, paths))):
            if (
                paths is None
                and relative.name.startswith(".env")
                and relative.name != ".env.example"
            ):
                found = True
                print(f"{relative.as_posix()}:0: FORBIDDEN_SECRET_FILE")
                continue
            if excluded(relative):
                continue
            try:
                path = root / relative
                if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
                    raise ValueError("不扫描越界路径或符号链接。")
                raw = git(root, "show", f":{relative.as_posix()}") if staged else path.read_bytes()
                if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
                    text = raw.decode("utf-16")
                elif b"\0" in raw:
                    continue
                else:
                    text = raw.decode("utf-8", errors="replace")
                count += 1
                for line, rule in inspect_text(text):
                    found = True
                    print(f"{relative.as_posix()}:{line}: {rule}")
            except Exception:  # noqa: BLE001 - 不回显 Git 输出、文件内容或异常正文
                failed = True
                print(f"{relative.as_posix()}: SCAN_ERROR")
    except Exception:  # noqa: BLE001
        print("扫描输入无效或读取失败。", file=sys.stderr)
        return 2
    print(
        f"已扫描 {count} 个文件；可疑项：{'有' if found else '无'}；读取错误：{'有' if failed else '无'}。"
    )
    return 2 if failed else 1 if found else 0


class ScanParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "扫描参数无效，请使用 --help 查看用法。\n")


def main() -> int:
    parser = ScanParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--staged", action="store_true", help="读取暂存区实际内容")
    mode.add_argument("--paths", nargs="+", help="扫描仓库内指定报告、日志或目录")
    args = parser.parse_args()
    return scan(ROOT, staged=args.staged, paths=args.paths)


if __name__ == "__main__":
    raise SystemExit(main())
