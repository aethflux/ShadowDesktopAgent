"""Semantic memory store backed by ChromaDB.

Design notes
------------
* Uses an explicit Chroma ``PersistentClient`` rooted at ``settings.chroma_dir`` so
  memory survives restarts.
* Collection is created with ``embedding_function=None`` — we compute embeddings
  ourselves via ``app.services.embeddings`` so the exact same vectors work in
  both the OpenAI and offline hash modes, and tests don't need network.
* Graceful degradation: if ``chromadb`` is not installed (e.g. minimal CI
  environment), ``VectorStore.available`` is ``False`` and all methods no-op so
  the rest of the system keeps working.
* One collection per ``session_id`` — keeps per-session memory isolated and
  avoids cross-session leakage.
"""
from __future__ import annotations

import hashlib
import time
from typing import Any

from app.config import settings
from app.services.embeddings import Embedder, get_embedder

try:
    import chromadb  # type: ignore

    _CHROMA_AVAILABLE = True
except Exception:  # pragma: no cover — import-time environment check
    chromadb = None  # type: ignore
    _CHROMA_AVAILABLE = False


class VectorStore:
    available: bool

    def __init__(self, embedder: Embedder | None = None) -> None:
        self.available = _CHROMA_AVAILABLE and settings.enable_semantic_memory
        self.embedder = embedder or get_embedder()
        if self.available:
            self._client = chromadb.PersistentClient(path=str(settings.chroma_dir))  # type: ignore[union-attr]
        else:
            self._client = None

    # ---- Collection helpers --------------------------------------------- #

    def _collection_name(self, session_id: str) -> str:
        # Chroma collection names must be [a-zA-Z0-9._-], 3..63 chars.
        safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in session_id)
        return f"mem_{safe}"[:63]

    def _get_collection(self, session_id: str):  # type: ignore[no-untyped-def]
        if not self.available:
            return None
        return self._client.get_or_create_collection(  # type: ignore[union-attr]
            name=self._collection_name(session_id),
            metadata={"hnsw:space": "cosine"},
        )

    # ---- Public API ------------------------------------------------------ #

    def add(
        self,
        session_id: str,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Index a piece of text under the given session."""
        if not self.available or not text.strip():
            return
        collection = self._get_collection(session_id)
        if collection is None:
            return
        doc_id = hashlib.md5(f"{session_id}:{time.time_ns()}:{text}".encode("utf-8")).hexdigest()
        embedding = self.embedder.embed_documents([text])[0]
        collection.add(
            ids=[doc_id],
            embeddings=[embedding],
            documents=[text],
            metadatas=[metadata or {"ts": time.time()}],
        )

    def query(self, session_id: str, query_text: str, top_k: int | None = None) -> list[dict[str, Any]]:
        """Return the top-k semantically similar memories for this session."""
        if not self.available or not query_text.strip():
            return []
        collection = self._get_collection(session_id)
        if collection is None:
            return []
        # Avoid querying an empty collection — Chroma would raise.
        try:
            count = collection.count()
        except Exception:
            count = 0
        if count == 0:
            return []
        k = min(top_k or settings.semantic_top_k, count)
        embedding = self.embedder.embed_query(query_text)
        result = collection.query(query_embeddings=[embedding], n_results=k)
        docs = (result.get("documents") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        hits: list[dict[str, Any]] = []
        for doc, meta, dist in zip(docs, metadatas, distances):
            hits.append(
                {
                    "text": doc,
                    "metadata": meta or {},
                    # Chroma returns cosine *distance*; convert to similarity.
                    "score": 1.0 - float(dist) if dist is not None else None,
                }
            )
        return hits

    def reset(self, session_id: str) -> None:
        if not self.available:
            return
        try:
            self._client.delete_collection(self._collection_name(session_id))  # type: ignore[union-attr]
        except Exception:
            pass
