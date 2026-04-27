from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Agent-core project root (directory containing app/ — parent of this file's parent)
_AGENT_CORE_ROOT = Path(__file__).resolve().parents[1]

Provider = Literal["vllm", "openai", "anthropic", "minimax", "modelscope"]
EmbeddingProvider = Literal["openai", "modelscope", "hash"]


class Settings(BaseSettings):
    app_name: str = "Bishoujo Agent Core"
    host: str = "127.0.0.1"
    port: int = 8787

    # ---- LLM provider ----
    # Switch between local vLLM / cloud OpenAI / Anthropic / MiniMax / ModelScope.
    provider: Provider = "minimax"
    model: str = "MiniMax-M2.7"
    api_base: str = "http://127.0.0.1:8000/v1"
    api_key: str = "EMPTY"
    openai_oauth_token: str | None = None

    # Provider-specific model overrides (optional). If set, override `model` per provider.
    openai_model: str | None = "gpt-4o-mini"
    anthropic_model: str | None = "claude-sonnet-4-20250514"
    vllm_model: str | None = "Qwen/Qwen2.5-14B-Instruct"
    minimax_model: str | None = "MiniMax-M2.7"
    modelscope_model: str | None = "Qwen/Qwen3-VL-8B-Instruct"

    # Provider-specific credentials and endpoints. Legacy `api_key` / `api_base`
    # remain as fallbacks so older local setups do not break.
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    vllm_api_key: str = "EMPTY"
    minimax_api_key: str | None = None
    modelscope_api_key: str | None = None

    openai_api_base: str = "https://api.openai.com/v1"
    anthropic_api_base: str = "https://api.anthropic.com"
    vllm_api_base: str = "http://127.0.0.1:8000/v1"
    minimax_api_base: str = "https://api.minimax.chat/v1"
    modelscope_api_base: str = "https://api-inference.modelscope.cn/v1"

    # Screen/image analysis can use a different provider from the main chat model.
    vision_provider: Provider = "modelscope"
    vision_model: str = "Qwen/Qwen3-VL-8B-Instruct"

    # Prompt caching (Anthropic explicit; OpenAI auto-cached when prefixes are stable).
    enable_prompt_cache: bool = True

    # ---- Embedding provider ----
    # "openai" = OpenAI embedding API, "modelscope" = ModelScope API-Inference,
    # "hash" = local zero-dep fallback.
    embedding_provider: EmbeddingProvider = "hash"
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 256  # only used by hash fallback

    # ---- Semantic memory ----
    enable_semantic_memory: bool = True
    semantic_top_k: int = 4
    chroma_dir: Path = Field(default=_AGENT_CORE_ROOT / "memory" / "chroma")

    # ---- Edge Neural TTS ----
    # Default cloud TTS. It does not require the MiniMax / ModelScope speech quota
    # and falls back to browser speech if the network call fails.
    enable_edge_tts: bool = True
    edge_tts_voice: str = "zh-CN-XiaoxiaoNeural"
    edge_tts_rate: str = "+0%"
    edge_tts_pitch: str = "+0Hz"

    # ---- Optional MiniMax TTS / ASR ----
    # Browser Web Speech remains the fallback. Enable this only when the current
    # MiniMax token-plan key has speech quota.
    enable_minimax_voice: bool = False
    minimax_voice_api_base: str = "https://api.minimax.chat/v1"
    minimax_tts_voice_id: str = "Chinese (Mandarin)_Warm_Girl"
    minimax_tts_model: str = "speech-2.8-hd"
    minimax_tts_speed: float = 1.0
    minimax_tts_pitch: float = 0.0
    minimax_tts_volume: float = 1.0
    minimax_asr_model: str = "whisper-1"  # whisper-1 or MiniMax's model

    # ---- Optional ModelScope TTS ----
    # Keep disabled by default: the public CosyVoice2 Studio can return silent
    # audio for API calls without a valid prompt voice sample.
    enable_modelscope_tts: bool = False
    modelscope_tts_api_base: str = "https://iic-cosyvoice2-0-5b.ms.show"
    modelscope_tts_model: str = "iic/CosyVoice2-0.5B"
    modelscope_tts_mode: str = "自然语言控制"
    modelscope_tts_instruction: str = "用温柔自然的中文女声朗读"
    modelscope_tts_seed: int = 0

    # ---- Optional Gemini TTS ----
    # Gemini 3.1 Flash TTS — enabled separately via ENABLE_GEMINI_TTS.
    # Uses the OpenAI-compatible `/v1/audio/speech` endpoint.
    enable_gemini_tts: bool = False
    gemini_tts_api_key: str | None = None
    gemini_tts_api_base: str = "https://generativelanguage.googleapis.com"
    gemini_tts_model: str = "google/gemini-3.1-flash-tts-preview"
    gemini_tts_voice: str = "Kore"
    gemini_tts_speed: float = 1.0
    gemini_tts_output_format: str = "mp3"

    # ---- UI / paths ----
    desktop_origin: str = "http://localhost:5173"
    memory_dir: Path = Field(default=_AGENT_CORE_ROOT / "memory")
    skills_dir: Path = Field(default=_AGENT_CORE_ROOT / "skills")
    screenshots_dir: Path = Field(default=_AGENT_CORE_ROOT / "artifacts" / "screenshots")
    command_timeout_seconds: int = 20

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    def resolved_model(self, provider: Provider | None = None) -> str:
        selected = provider or self.provider
        if selected == "openai" and self.openai_model:
            return self.openai_model
        if selected == "anthropic" and self.anthropic_model:
            return self.anthropic_model
        if selected == "vllm" and self.vllm_model:
            return self.vllm_model
        if selected == "minimax" and self.minimax_model:
            return self.minimax_model
        if selected == "modelscope" and self.modelscope_model:
            return self.modelscope_model
        return self.model

    @staticmethod
    def _clean_base(url: str) -> str:
        return url.rstrip("/")

    def resolved_api_key(self, provider: Provider | None = None) -> str:
        selected = provider or self.provider
        if selected == "openai":
            return self.openai_oauth_token or self.openai_api_key or self.api_key
        if selected == "anthropic":
            return self.anthropic_api_key or self.api_key
        if selected == "vllm":
            return self.vllm_api_key or self.api_key
        if selected == "minimax":
            return self.minimax_api_key or self.api_key
        if selected == "modelscope":
            return self.modelscope_api_key or self.api_key
        return self.api_key

    def resolved_api_base(self, provider: Provider | None = None) -> str:
        selected = provider or self.provider
        if selected == "openai":
            return self._clean_base(self.openai_api_base)
        if selected == "anthropic":
            return self._clean_base(self.anthropic_api_base)
        if selected == "vllm":
            return self._clean_base(self.vllm_api_base or self.api_base)
        if selected == "minimax":
            return self._clean_base(self.minimax_api_base)
        if selected == "modelscope":
            return self._clean_base(self.modelscope_api_base)
        return self._clean_base(self.api_base)

    def resolved_embedding_api_key(self) -> str:
        if self.embedding_provider == "openai":
            return self.resolved_api_key("openai")
        if self.embedding_provider == "modelscope":
            return self.modelscope_api_key or self.api_key
        return self.resolved_api_key("vllm")

    def resolved_embedding_api_base(self) -> str:
        if self.embedding_provider == "openai":
            return self.resolved_api_base("openai")
        if self.embedding_provider == "modelscope":
            return self._clean_base(self.modelscope_api_base)
        return self.resolved_api_base("vllm")


settings = Settings()
settings.memory_dir.mkdir(parents=True, exist_ok=True)
settings.skills_dir.mkdir(parents=True, exist_ok=True)
settings.screenshots_dir.mkdir(parents=True, exist_ok=True)
settings.chroma_dir.mkdir(parents=True, exist_ok=True)
