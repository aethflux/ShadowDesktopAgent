"""Embedding providers for semantic memory.

Three implementations, switchable via ``EMBEDDING_PROVIDER``:

- ``openai``     — OpenAI official embedding API
- ``modelscope`` — ModelScope API-Inference embedding API
- ``hash``       — zero-dependency local fallback (MD5 n-gram → L2-norm vector).
  Deterministic, offline, sufficient for CI and demos.

All implementations expose the same ``embed(texts)`` synchronous interface so the
chroma collection can call them uniformly.
"""
from __future__ import annotations

import hashlib
import math
import re
from typing import Any, Protocol

import httpx

from app.config import settings


class Embedder(Protocol):
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


# --------------------------------------------------------------------------- #
#  Hash fallback (offline, zero deps beyond stdlib)
# --------------------------------------------------------------------------- #

_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)


class HashEmbedder:
    """Bag-of-hashed-tokens embedder with L2 normalization.

    Not a replacement for a real embedding model, but good enough to make
    cosine similarity return sensible neighbors on short Chinese/English text.
    Cheap and offline — ideal for CI and local-only demos.
    """

    def __init__(self, dim: int | None = None) -> None:
        self.dim = dim or settings.embedding_dim

    def _tokens(self, text: str) -> list[str]:
        text = text.lower()
        unigrams = _TOKEN_RE.findall(text)
        # Character bigrams help for CJK where tokenization is fuzzy.
        bigrams = [text[i : i + 2] for i in range(len(text) - 1) if text[i].strip() and text[i + 1].strip()]
        return unigrams + bigrams

    def _embed_one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for token in self._tokens(text):
            h = hashlib.md5(token.encode("utf-8")).digest()
            idx = int.from_bytes(h[:4], "little") % self.dim
            sign = 1.0 if h[4] & 1 else -1.0
            vec[idx] += sign
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.embed(texts)

    def embed_query(self, text: str) -> list[float]:
        return self.embed([text])[0]


# --------------------------------------------------------------------------- #
#  OpenAI-compatible embeddings (OpenAI / ModelScope)
# --------------------------------------------------------------------------- #

class OpenAICompatEmbedder:
    """Call an OpenAI-compatible embedding endpoint.

    ModelScope's `/embeddings` endpoint is close to OpenAI's schema, but it
    requires `encoding_format="float"`, so we include that only for the
    ModelScope provider.
    """

    def __init__(self) -> None:
        self.model = settings.embedding_model
        # OpenAI text-embedding-3-small / Qwen3-Embedding-0.6B return 1536 / 1024.
        # The caller stores whatever the API returns; chroma does cosine anyway.
        self.dim = 1536

    def _base_url(self) -> str:
        return settings.resolved_embedding_api_base()

    def _request_payload(self, texts: list[str]) -> dict[str, Any]:
        payload: dict[str, Any] = {"model": self.model, "input": texts}
        if settings.embedding_provider == "modelscope":
            payload["encoding_format"] = "float"
        return payload

    def embed(self, texts: list[str]) -> list[list[float]]:
        token = settings.resolved_embedding_api_key()
        response = httpx.post(
            f"{self._base_url()}/embeddings",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=self._request_payload(texts),
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()["data"]
        return [item["embedding"] for item in data]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.embed(texts)

    def embed_query(self, text: str) -> list[float]:
        return self.embed([text])[0]


def get_embedder() -> Embedder:
    if settings.embedding_provider in {"openai", "modelscope"}:
        return OpenAICompatEmbedder()
    return HashEmbedder()
