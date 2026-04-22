from __future__ import annotations

from typing import Any

import httpx

from app.services.embeddings import OpenAICompatEmbedder


def test_modelscope_embedder_sends_encoding_format(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {
                "data": [
                    {"embedding": [0.1, 0.2, 0.3]},
                ]
            }

    def fake_post(url: str, headers: dict[str, str], json: dict[str, Any], timeout: int):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr("app.services.embeddings.settings.embedding_provider", "modelscope")
    monkeypatch.setattr("app.services.embeddings.settings.embedding_model", "Qwen/Qwen3-Embedding-0.6B")

    embedder = OpenAICompatEmbedder()
    vector = embedder.embed_query("hello")

    assert vector == [0.1, 0.2, 0.3]
    assert captured["json"]["model"] == "Qwen/Qwen3-Embedding-0.6B"
    assert captured["json"]["input"] == ["hello"]
    assert captured["json"]["encoding_format"] == "float"
