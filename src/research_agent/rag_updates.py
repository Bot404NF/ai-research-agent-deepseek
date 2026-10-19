"""解析并增量写入用户新增的 RAG 资料。"""

import hashlib
import json
import shutil
import uuid
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from threading import RLock

from langchain_core.documents import Document
from langchain_milvus import Milvus
from langchain_text_splitters import RecursiveCharacterTextSplitter

from research_agent.config import Settings
from research_agent.embeddings import create_embeddings
from research_agent.ingest import INGEST_BATCH_SIZE, SUPPORTED_SUFFIXES, load_file
from research_agent.storage import write_json_atomic


MAX_FILE_SIZE = 100 * 1024 * 1024
_MUTATION_LOCK = RLock()


def _serialized_update(function):
    """串行处理本进程的资料库修改，避免清单读改写丢失其他请求。"""
    @wraps(function)
    def wrapped(*args, **kwargs):
        with _MUTATION_LOCK:
            return function(*args, **kwargs)
    return wrapped


def incoming_directory(settings: Settings) -> Path:
    return settings.data_dir.parent / "incoming"


def _databases_path(settings: Settings) -> Path:
    return settings.data_dir.parent / ".rag_databases.json"


def _deleted_directories_path(settings: Settings) -> Path:
    return settings.data_dir.parent / ".rag_deleted_directories.json"


def _read_deleted_directories(settings: Settings) -> set[str]:
    path = _deleted_directories_path(settings)
    if not path.exists():
        return set()
    values = json.loads(path.read_text(encoding="utf-8"))
    return set(values) if isinstance(values, list) else set()


def _write_deleted_directories(settings: Settings, deleted: set[str]) -> None:
    path = _deleted_directories_path(settings)
    write_json_atomic(path, sorted(deleted))


def list_databases(settings: Settings) -> list[dict]:
    """列出现有资料库及简要信息。"""
    path = _databases_path(settings)
    databases = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    additions = {
        digest: item
        for digest, item in _read_manifest(settings).get("completed", {}).items()
        if item.get("folder_id", "legacy-additions") == "legacy-additions"
    }
    result = []
    if "initial" not in _read_deleted_directories(settings):
        result.append({"id": "initial", "name": "上海地方志", "collection": settings.collection_name, "type": "原始资料", "files": 0, "chunks": 0})
    if additions:
        result.append({"id": "legacy-additions", "name": "补充资料", "collection": settings.additions_collection_name, "type": "补充资料", "files": len(additions), "chunks": sum(int(item.get("chunks", 0)) for item in additions.values())})
    for database in databases:
        database["files"] = len(database.get("completed", {}))
        database["chunks"] = sum(int(item.get("chunks", 0)) for item in database.get("completed", {}).values())
        result.append({key: database[key] for key in ("id", "name", "collection", "type", "files", "chunks")})
    return result


def database_details(settings: Settings) -> list[dict]:
    """提供资料库的文件清单、索引状态和最近更新时间。"""
    from pymilvus import MilvusClient

    catalog = list_databases(settings)
    client = None
    try:
        client = MilvusClient(uri=settings.milvus_uri, timeout=3)
        collections = set(client.list_collections())
        collection_counts = {}
        shared_manifest = _read_manifest(settings)
        db_file = _databases_path(settings)
        entries = json.loads(db_file.read_text(encoding="utf-8")) if db_file.exists() else []
        entries_by_id = {item["id"]: item for item in entries}
        for database in catalog:
            collection = database["collection"]
            database["status"] = "已就绪" if collection in collections else "未建立索引"
            if collection in collections:
                try:
                    if collection not in collection_counts:
                        collection_counts[collection] = client.get_collection_stats(collection_name=collection)
                    database["chunks"] = int(collection_counts[collection].get("row_count", database["chunks"]))
                except Exception:
                    pass
            database["updated_at"] = None
            if database["id"] == "initial":
                files = sorted(path.relative_to(settings.data_dir).as_posix() for path in settings.data_dir.rglob("*") if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES) if settings.data_dir.exists() else []
                indexed = [{"source": name, "chunks": None, "updated_at": None, "status": database["status"], "hash": None, "data_version": "original"} for name in files]
                manifest = shared_manifest
                for digest, item in manifest.get("completed", {}).items():
                    if item.get("folder_id") == "initial":
                        indexed.append({**item, "hash": digest, "status": "已就绪", "data_version": item.get("data_version", "supplement")})
                for digest, item in manifest.get("pending", {}).items():
                    if item.get("folder_id") == "initial":
                        indexed.append({**item, "hash": digest, "status": "待核查", "data_version": item.get("data_version", "supplement")})
                database["indexed_files"] = sorted(indexed, key=lambda item: item["source"])
                database["files"] = len(indexed)
                continue
            if database["id"] == "legacy-additions":
                all_additions = shared_manifest
                manifest = {
                    section: {
                        digest: item for digest, item in all_additions.get(section, {}).items()
                        if item.get("folder_id", "legacy-additions") == "legacy-additions"
                    }
                    for section in ("completed", "pending")
                }
            else:
                entry = entries_by_id.get(database["id"], {})
                manifest = {"completed": entry.get("completed", {}), "pending": entry.get("pending", {})}
            if manifest.get("pending"):
                database["status"] = "待核查"
            indexed = []
            for digest, item in manifest.get("completed", {}).items():
                version = "supplement" if database["id"] == "legacy-additions" else item.get("data_version", "original")
                indexed.append({"source": item["source"], "chunks": int(item.get("chunks", 0)), "updated_at": item.get("updated_at"), "status": "已就绪", "hash": digest, "data_version": version})
            for digest, item in manifest.get("pending", {}).items():
                version = "supplement" if database["id"] == "legacy-additions" else item.get("data_version", "original")
                indexed.append({"source": item["source"], "chunks": int(item.get("chunks", 0)), "updated_at": item.get("updated_at"), "status": "待核查", "hash": digest, "data_version": version})
            database["indexed_files"] = sorted(indexed, key=lambda item: item["source"])
            database["files"] = len(manifest.get("completed", {}))
            times = [item["updated_at"] for item in indexed if item.get("updated_at")]
            database["updated_at"] = max(times, default=None)
        return catalog
    except Exception:
        for database in catalog:
            database.setdefault("status", "状态未知")
            database.setdefault("updated_at", None)
            database.setdefault("indexed_files", [])
        return catalog
    finally:
        if client is not None:
            client.close()


@_serialized_update
def create_database(name: str, settings: Settings) -> dict:
    name = name.strip()
    if not name or len(name) > 80:
        raise ValueError("目录名称不能为空且不能超过 80 个字符。")
    path = _databases_path(settings)
    databases = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    if any(item["name"] == name for item in list_databases(settings)):
        raise ValueError("目录名称已存在，请使用其他名称。")
    identifier = uuid.uuid4().hex[:12]
    # 新目录共享一个补充资料集合；目录归属保存在每个文本块的元数据中。
    entry = {"id": identifier, "name": name, "collection": settings.additions_collection_name, "type": "目录", "completed": {}, "pending": {}}
    path.parent.mkdir(parents=True, exist_ok=True)
    databases.append(entry)
    write_json_atomic(path, databases)
    (incoming_directory(settings) / identifier).mkdir(parents=True, exist_ok=True)
    return {"id": identifier, "name": name, "collection": entry["collection"], "type": "目录", "files": 0}


@_serialized_update
def delete_database(database_id: str, settings: Settings) -> bool:
    """删除目录、其导入文件和对应的 Milvus 文本块。"""
    if database_id == "initial":
        return _delete_initial_directory(settings)
    if database_id == "legacy-additions":
        raise ValueError("此旧版补充资料目录不能单独删除。")
    databases_path = _databases_path(settings)
    databases = json.loads(databases_path.read_text(encoding="utf-8")) if databases_path.exists() else []
    database = next((item for item in databases if item.get("id") == database_id), None)
    if database is None:
        return False

    protected_ids: dict[str, set[str]] = {}
    for other in databases:
        if other.get("id") == database_id:
            continue
        for section in ("completed", "pending"):
            for digest, item in other.get(section, {}).items():
                collection = item.get("collection", other.get("collection", settings.additions_collection_name))
                prefix = item.get("id_prefix", digest[:48])
                protected_ids.setdefault(collection, set()).update(
                    f"{prefix}-{index:08d}" for index in range(int(item.get("chunks", 0)))
                )
    shared_manifest = _read_manifest(settings)
    for section in ("completed", "pending"):
        for digest, item in shared_manifest.get(section, {}).items():
            if item.get("folder_id", "legacy-additions") == database_id:
                continue
            collection = item.get("collection", settings.additions_collection_name)
            prefix = item.get("id_prefix", digest[:48])
            protected_ids.setdefault(collection, set()).update(
                f"{prefix}-{index:08d}" for index in range(int(item.get("chunks", 0)))
            )

    groups: dict[str, list[str]] = {}
    for section in ("completed", "pending"):
        for digest, item in database.get(section, {}).items():
            collection = item.get("collection", database.get("collection", settings.additions_collection_name))
            count = int(item.get("chunks", 0))
            prefix = item.get("id_prefix", digest[:48])
            ids = [f"{prefix}-{index:08d}" for index in range(count)]
            groups.setdefault(collection, []).extend(
                vector_id for vector_id in ids if vector_id not in protected_ids.get(collection, set())
            )
    if groups:
        from pymilvus import MilvusClient

        client = MilvusClient(uri=settings.milvus_uri, timeout=3)
        try:
            for collection, vector_ids in groups.items():
                if vector_ids and client.has_collection(collection_name=collection):
                    client.delete(collection_name=collection, ids=vector_ids)
        finally:
            client.close()

    incoming_root = incoming_directory(settings).resolve()
    folder_path = (incoming_root / database_id).resolve()
    try:
        folder_path.relative_to(incoming_root)
    except ValueError as exc:
        raise ValueError("目录路径无效，无法删除。") from exc
    if folder_path.exists():
        shutil.rmtree(folder_path)
    databases = [item for item in databases if item.get("id") != database_id]
    write_json_atomic(databases_path, databases)
    return True


def _delete_initial_directory(settings: Settings) -> bool:
    """删除上海地方志原始资料目录及该目录下的补充资料。"""
    deleted = _read_deleted_directories(settings)
    if "initial" in deleted:
        return False

    source_directory = settings.data_dir.resolve()
    if source_directory == source_directory.parent or not source_directory.name:
        raise ValueError("资料目录路径无效，已拒绝删除。")
    protected_paths = (
        incoming_directory(settings),
        settings.conversation_dir,
        _databases_path(settings),
        _manifest_path(settings),
        _deleted_directories_path(settings),
    )
    if any(path.resolve().is_relative_to(source_directory) for path in protected_paths):
        raise ValueError("资料目录包含应用的其他数据，已拒绝删除。")

    manifest = _read_manifest(settings)
    initial_items = {
        digest: item
        for section in ("completed", "pending")
        for digest, item in manifest.get(section, {}).items()
        if item.get("folder_id") == "initial"
    }
    protected_ids: set[str] = set()
    for section in ("completed", "pending"):
        for digest, item in manifest.get(section, {}).items():
            if item.get("folder_id") == "initial":
                continue
            prefix = item.get("id_prefix", digest[:48])
            protected_ids.update(f"{prefix}-{index:08d}" for index in range(int(item.get("chunks", 0))))
    databases_path = _databases_path(settings)
    databases = json.loads(databases_path.read_text(encoding="utf-8")) if databases_path.exists() else []
    for database in databases:
        for section in ("completed", "pending"):
            for digest, item in database.get(section, {}).items():
                if item.get("collection", database.get("collection")) != settings.additions_collection_name:
                    continue
                prefix = item.get("id_prefix", digest[:48])
                protected_ids.update(f"{prefix}-{index:08d}" for index in range(int(item.get("chunks", 0))))

    supplement_ids = []
    for digest, item in initial_items.items():
        prefix = item.get("id_prefix", digest[:48])
        supplement_ids.extend(
            vector_id
            for index in range(int(item.get("chunks", 0)))
            if (vector_id := f"{prefix}-{index:08d}") not in protected_ids
        )

    from pymilvus import MilvusClient

    client = MilvusClient(uri=settings.milvus_uri, timeout=3)
    try:
        if client.has_collection(collection_name=settings.collection_name):
            client.drop_collection(collection_name=settings.collection_name)
        if supplement_ids and client.has_collection(collection_name=settings.additions_collection_name):
            client.delete(collection_name=settings.additions_collection_name, ids=supplement_ids)
    finally:
        client.close()

    incoming_root = incoming_directory(settings).resolve()
    initial_uploads = (incoming_root / "initial").resolve()
    initial_uploads.relative_to(incoming_root)
    if initial_uploads.exists():
        shutil.rmtree(initial_uploads)
    if source_directory.exists():
        shutil.rmtree(source_directory)

    for section in ("completed", "pending"):
        manifest[section] = {
            digest: item for digest, item in manifest.get(section, {}).items()
            if item.get("folder_id") != "initial"
        }
    _write_manifest(settings, manifest)
    deleted.add("initial")
    _write_deleted_directories(settings, deleted)
    return True


def list_incoming_files(settings: Settings, database_id: str | None = None) -> list[str]:
    """列出待添加目录中的支持文件相对路径。"""
    directory = incoming_directory(settings)
    if not directory.exists():
        return []
    if database_id and database_id != "legacy-additions":
        directory = directory / database_id
    candidates = directory.glob("*") if not database_id else directory.rglob("*")
    return sorted(
        path.relative_to(directory).as_posix()
        for path in candidates
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
    )


def _manifest_path(settings: Settings) -> Path:
    return settings.data_dir.parent / ".rag_additions_manifest.json"


def _read_manifest(settings: Settings) -> dict:
    path = _manifest_path(settings)
    if not path.exists():
        return {"completed": {}, "pending": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("新增资料索引清单格式无效。")
    data.setdefault("completed", {})
    data.setdefault("pending", {})
    return data


def _write_manifest(settings: Settings, manifest: dict) -> None:
    path = _manifest_path(settings)
    write_json_atomic(path, manifest)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@_serialized_update
def add_file_to_rag(relative_path: str, settings: Settings, database_id: str | None = None, replace_hash: str | None = None, data_version: str = "supplement") -> dict[str, int | str | bool]:
    """解析 incoming 中的单个文件并追加到新增资料集合，不重建旧集合。"""
    if settings.additions_collection_name == settings.collection_name:
        raise ValueError("新增资料集合必须与原资料集合使用不同名称。")
    if data_version not in {"original", "supplement"}:
        raise ValueError("资料标签无效。")
    database = None
    if database_id and database_id not in {"legacy-additions", "initial"}:
        databases_path = _databases_path(settings)
        databases = json.loads(databases_path.read_text(encoding="utf-8")) if databases_path.exists() else []
        database = next((item for item in databases if item["id"] == database_id), None)
        if database is None:
            raise ValueError("所选资料库不存在。")
    incoming_root = incoming_directory(settings) / database_id if database_id else incoming_directory(settings)
    incoming = incoming_root.resolve()
    supplied = Path(relative_path)
    if supplied.is_absolute():
        raise ValueError("请提供相对于 data/incoming/ 的文件路径。")
    path = (incoming / supplied).resolve()
    try:
        path.relative_to(incoming)
    except ValueError as exc:
        raise ValueError("文件必须位于 data/incoming/ 目录内。") from exc
    if not path.is_file():
        raise FileNotFoundError(f"待添加文件不存在：{relative_path}")
    if path.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise ValueError("只支持 .txt、.md 和 .pdf 文件；扫描版 PDF 暂不支持 OCR。")
    if path.stat().st_size > MAX_FILE_SIZE:
        raise ValueError("文件超过 100 MB 限制，请拆分后再添加。")

    file_hash = _file_sha256(path)
    if database is None:
        manifest = _read_manifest(settings)
    else:
        manifest = {"completed": database.setdefault("completed", {}), "pending": database.setdefault("pending", {})}
    if replace_hash and replace_hash not in manifest["completed"]:
        raise ValueError("待更新的索引文件已不存在，请刷新管理页后重试。")
    if replace_hash and file_hash in manifest["completed"] and file_hash != replace_hash:
        raise ValueError("该文件内容已在当前资料库建立索引；请检查文件清单后再更新。")
    if file_hash in manifest["completed"] and (not replace_hash or replace_hash == file_hash):
        item = manifest["completed"][file_hash]
        return {"added": False, "updated": False, "source": item["source"], "chunks": int(item["chunks"])}
    if file_hash in manifest["pending"]:
        raise RuntimeError(
            "此文件上次索引状态不确定；为避免重复写入，请先检查新增资料集合后再处理。"
        )

    documents = load_file(path, settings.data_dir.parent.resolve())
    if not documents:
        raise ValueError("文件中没有可提取的文字；扫描版 PDF 需要先单独完成 OCR。")

    folder_name = database["name"] if database else ("上海地方志" if database_id == "initial" else "补充资料")
    source = f"{folder_name}/{path.relative_to(incoming).as_posix()}"
    normalized = [
        Document(
            page_content=document.page_content,
            metadata={
                "source": source,
                "page": int(document.metadata.get("page", 0)),
                "data_version": data_version,
                "file_sha256": file_hash,
                "folder_id": database_id or "legacy-additions",
                "folder_name": folder_name,
            },
        )
        for document in documents
    ]
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        add_start_index=True,
    )
    chunks = splitter.split_documents(normalized)
    if not chunks:
        raise ValueError("文件没有生成可索引的文本块。")

    id_prefix = hashlib.sha256(f"{database_id or 'legacy-additions'}:{file_hash}".encode("utf-8")).hexdigest()[:48]
    manifest["pending"][file_hash] = {
        "source": source,
        "chunks": len(chunks),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "data_version": data_version,
        "folder_id": database_id or "legacy-additions",
        "collection": settings.additions_collection_name,
        "id_prefix": id_prefix,
    }
    def save_manifest() -> None:
        if database is None:
            _write_manifest(settings, manifest)
        else:
            database["completed"] = manifest["completed"]
            database["pending"] = manifest["pending"]
            write_json_atomic(databases_path, databases)
    save_manifest()

    target_collection = settings.additions_collection_name
    store = Milvus(
        embedding_function=create_embeddings(settings.embedding_model),
        collection_name=target_collection,
        connection_args={"uri": settings.milvus_uri},
        drop_old=False,
        auto_id=False,
    )
    for start in range(0, len(chunks), INGEST_BATCH_SIZE):
        batch = chunks[start : start + INGEST_BATCH_SIZE]
        ids = [f"{id_prefix}-{index:08d}" for index in range(start, start + len(batch))]
        store.add_documents(batch, ids=ids)

    if replace_hash:
        previous = manifest["completed"].get(replace_hash)
        if not previous:
            raise ValueError("待更新的索引文件已不存在，请刷新管理页后重试。")
        previous_prefix = previous.get("id_prefix", replace_hash[:48])
        old_ids = [f"{previous_prefix}-{index:08d}" for index in range(int(previous["chunks"]))]
        if old_ids:
            old_collection = previous.get("collection", database.get("collection", target_collection) if database else target_collection)
            delete_store = store if old_collection == target_collection else Milvus(
                embedding_function=create_embeddings(settings.embedding_model),
                collection_name=old_collection,
                connection_args={"uri": settings.milvus_uri},
                drop_old=False,
                auto_id=False,
            )
            delete_store.delete(ids=old_ids)
        manifest["completed"].pop(replace_hash, None)

    manifest["completed"][file_hash] = manifest["pending"].pop(file_hash)
    manifest["completed"][file_hash]["collection"] = target_collection
    manifest["completed"][file_hash]["updated_at"] = datetime.now(timezone.utc).isoformat()
    save_manifest()
    return {"added": True, "updated": bool(replace_hash), "source": source, "chunks": len(chunks)}


