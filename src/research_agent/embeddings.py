import os
from functools import lru_cache
from threading import RLock

import torch
from langchain_huggingface import HuggingFaceEmbeddings


_MODEL_LOCK = RLock()


def create_embeddings(model_name: str) -> HuggingFaceEmbeddings:
    """复用当前进程的本地模型；设备设为自动时优先使用 CUDA。"""
    device = os.getenv("BGE_M3_DEVICE", "auto").strip().lower()
    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"
    # 缓存以解析后的设备为键；锁避免并发首次请求重复加载模型。
    with _MODEL_LOCK:
        return _cached_embeddings(model_name, device)


@lru_cache(maxsize=1)
def _cached_embeddings(model_name: str, device: str) -> HuggingFaceEmbeddings:
    return HuggingFaceEmbeddings(
        model_name=model_name,
        model_kwargs={"device": device},
        encode_kwargs={"normalize_embeddings": True, "batch_size": 16},
    )
