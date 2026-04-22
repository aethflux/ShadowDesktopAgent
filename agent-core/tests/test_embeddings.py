"""HashEmbedder: deterministic, normalized, dimensionally stable."""
from __future__ import annotations

import math

from app.services.embeddings import HashEmbedder


def test_hash_embedder_is_deterministic() -> None:
    e = HashEmbedder(dim=128)
    v1 = e.embed(["hello world"])[0]
    v2 = e.embed(["hello world"])[0]
    assert v1 == v2


def test_hash_embedder_is_unit_normalized() -> None:
    e = HashEmbedder(dim=128)
    v = e.embed(["semantic memory test"])[0]
    norm = math.sqrt(sum(x * x for x in v))
    assert abs(norm - 1.0) < 1e-6 or norm == 0.0


def test_similar_texts_have_higher_cosine_than_unrelated() -> None:
    e = HashEmbedder(dim=512)
    vecs = e.embed(
        [
            "我想做一个桌面 AI Agent",
            "桌面 Agent 项目进度",
            "今晚吃什么好呢",
        ]
    )

    def cosine(a: list[float], b: list[float]) -> float:
        return sum(x * y for x, y in zip(a, b))

    related = cosine(vecs[0], vecs[1])
    unrelated = cosine(vecs[0], vecs[2])
    assert related > unrelated
