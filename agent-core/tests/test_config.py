from __future__ import annotations

from app.config import Settings


def test_settings_accept_modelscope_embedding_provider() -> None:
    settings = Settings(
        _env_file=None,
        provider="minimax",
        minimax_api_key="EMG-test-key",
        embedding_provider="modelscope",
        embedding_model="Qwen/Qwen3-Embedding-0.6B",
        modelscope_api_key="ms-test-token",
    )

    assert settings.embedding_provider == "modelscope"
    assert settings.resolved_embedding_api_key() == "ms-test-token"
    assert settings.resolved_embedding_api_base() == "https://api-inference.modelscope.cn/v1"
