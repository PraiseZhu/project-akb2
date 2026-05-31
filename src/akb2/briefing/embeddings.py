"""嵌入客户端：批量调 AI Gateway /v1/embeddings + 文本 hash 磁盘缓存 + 失败抛错。

设计（P1-1，语义打分地基）：
- 镜像 synthesis._call_llm 的网关范式：httpx + AI_GATEWAY_API_KEY + AI_GATEWAY_BASE_URL。
- 批量：一次请求传文本数组，返回对应向量（顺序一致）。
- 缓存：按 sha256(model + text) 做磁盘缓存，命中不重复调网关；跨进程持久；按 model 隔离。
- 降级契约：网关失败 → **抛错**，由调用方（scoring）回退关键词版，绝不静默吞错、绝不污染缓存。

调用方约定（P1-3/P1-4/P2-2）：
    client = EmbeddingClient()
    vecs = client.embed(["text a", "text b", ...])   # 失败抛异常
    sim = cosine(vecs[0], anchor_vec)

环境变量：
- AKB2_EMBED_MODEL        默认 text-embedding-3-small（多语言，1536 维）
- AKB2_EMBED_CACHE_DIR    默认 /tmp/akb2-embed-cache
- AI_GATEWAY_API_KEY      网关凭证（缺失 → _embed_api 抛错）
- AI_GATEWAY_BASE_URL     默认 https://llm-proxy.tapsvc.com
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path

_DEFAULT_MODEL = os.environ.get("AKB2_EMBED_MODEL", "text-embedding-3-small")
_DEFAULT_CACHE_DIR = os.environ.get("AKB2_EMBED_CACHE_DIR", "/tmp/akb2-embed-cache")
_DEFAULT_TIMEOUT = int(os.environ.get("AKB2_EMBED_TIMEOUT", "60"))
_MAX_BATCH = int(os.environ.get("AKB2_EMBED_MAX_BATCH", "128"))


# ---------------------------------------------------------------------------
# 网关调用（可 patch 的真实 HTTP 入口）
# ---------------------------------------------------------------------------

def _embed_api(
    texts: list[str],
    *,
    model: str = _DEFAULT_MODEL,
    base_url: str | None = None,
    api_key: str | None = None,
    timeout: int = _DEFAULT_TIMEOUT,
) -> list[list[float]]:
    """调网关 /v1/embeddings，返回与 texts 等长、同序的向量列表。失败抛错。"""
    import httpx

    api_key = api_key or os.environ.get("AI_GATEWAY_API_KEY")
    if not api_key:
        raise RuntimeError("AI_GATEWAY_API_KEY 未设置，无法调用 /v1/embeddings")
    base_url = (base_url or os.environ.get("AI_GATEWAY_BASE_URL", "https://llm-proxy.tapsvc.com")).rstrip("/")
    url = f"{base_url}/v1/embeddings"

    resp = httpx.post(
        url,
        json={"model": model, "input": list(texts)},
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json().get("data", [])
    # 按 index 排序，确保与输入同序
    data_sorted = sorted(data, key=lambda d: d.get("index", 0))
    vecs = [d["embedding"] for d in data_sorted]
    if len(vecs) != len(texts):
        raise RuntimeError(
            f"embeddings 返回数量不符：请求 {len(texts)} 条，返回 {len(vecs)} 条"
        )
    return vecs


# ---------------------------------------------------------------------------
# cosine 相似度（scoring 语义维度复用）
# ---------------------------------------------------------------------------

def cosine(a: list[float], b: list[float]) -> float:
    """余弦相似度；任一为零向量返回 0.0。"""
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


# ---------------------------------------------------------------------------
# EmbeddingClient
# ---------------------------------------------------------------------------

class EmbeddingClient:
    def __init__(
        self,
        model: str | None = None,
        cache_dir: str | os.PathLike | None = None,
        timeout: int = _DEFAULT_TIMEOUT,
    ) -> None:
        self.model = model or _DEFAULT_MODEL
        self.cache_dir = Path(cache_dir) if cache_dir is not None else Path(_DEFAULT_CACHE_DIR)
        self.timeout = timeout
        self._mem: dict[str, list[float]] = {}

    # -- cache helpers ------------------------------------------------------

    def _key(self, text: str) -> str:
        h = hashlib.sha256(f"{self.model}\x00{text}".encode("utf-8")).hexdigest()
        return h

    def _cache_path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    def _load_cached(self, key: str) -> list[float] | None:
        if key in self._mem:
            return self._mem[key]
        p = self._cache_path(key)
        if p.exists():
            try:
                vec = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(vec, list):
                    self._mem[key] = vec
                    return vec
            except Exception:
                return None  # 损坏缓存当作 miss
        return None

    def _store_cached(self, key: str, vec: list[float]) -> None:
        self._mem[key] = vec
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            self._cache_path(key).write_text(json.dumps(vec), encoding="utf-8")
        except Exception:
            pass  # 缓存写失败不致命（内存仍有）

    # -- public -------------------------------------------------------------

    def embed(self, texts: list[str]) -> list[list[float]]:
        """返回与 texts 等长、同序的向量列表。命中缓存的不重复调网关；任何网关失败抛错。"""
        keys = [self._key(t) for t in texts]
        result: dict[str, list[float]] = {}

        # 1) 缓存命中
        for t, k in zip(texts, keys):
            cached = self._load_cached(k)
            if cached is not None:
                result[k] = cached

        # 2) 收集 miss（按首次出现顺序、去重）
        miss_texts: list[str] = []
        miss_keys: list[str] = []
        seen: set[str] = set()
        for t, k in zip(texts, keys):
            if k not in result and k not in seen:
                seen.add(k)
                miss_texts.append(t)
                miss_keys.append(k)

        # 3) 批量取 miss（分批，单批上限 _MAX_BATCH）
        for start in range(0, len(miss_texts), _MAX_BATCH):
            batch_texts = miss_texts[start:start + _MAX_BATCH]
            batch_keys = miss_keys[start:start + _MAX_BATCH]
            vecs = _embed_api(
                batch_texts,
                model=self.model,
                timeout=self.timeout,
            )
            for k, vec in zip(batch_keys, vecs):
                self._store_cached(k, vec)
                result[k] = vec

        # 4) 按输入顺序组装
        return [result[k] for k in keys]
