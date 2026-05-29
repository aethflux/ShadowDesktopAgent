from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Agent-core project root (directory containing app/ — parent of this file's parent)
_AGENT_CORE_ROOT = Path(__file__).resolve().parents[1]

Provider = Literal["vllm", "openai", "anthropic", "minimax", "modelscope"]
EmbeddingProvider = Literal["openai", "modelscope", "hash"]


class Settings(BaseSettings):
    app_name: str = "Shadow Agent Core"
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

    # ---- Reliability knobs ----
    # Max retries (per HTTP call) on transient failures (5xx, 429, network).
    # Set to 1 to disable retry behaviour entirely.
    model_max_retries: int = 3
    # Initial backoff in seconds; the actual delay grows exponentially.
    model_retry_backoff_seconds: float = 0.4
    # Anthropic max_tokens for chat completions.
    anthropic_max_tokens: int = 2048
    # Maximum age (in hours) of generated TTS audio files before
    # the server cleans them up at startup. Set to 0 to keep forever.
    tts_audio_retention_hours: int = 24

    # ---- Rate limiting ----
    # Token-bucket per-IP limiter. ``capacity`` tokens fill at
    # ``refill_per_second``. Disable by setting capacity to 0.
    rate_limit_capacity: int = 30
    rate_limit_refill_per_second: float = 0.5

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
    command_workspace_root: Path = Field(default=_AGENT_CORE_ROOT.parent)
    enable_gui_automation: bool = False
    external_cli_allowlist: str = "git,node,npm,npx,python,python3,py,pwsh,powershell"
    enable_filesystem_mcp: bool = True
    mcp_servers_json: str = ""
    mcp_tool_denylist: str = "write_file,edit_file,create_directory,move_file"
    mcp_tool_blocked_keywords: str = "write,edit,delete,remove,move,create,rename"

    # ---- Persona ----
    # Full PersonaConfig serialised as JSON. Empty string ⇒ defaults
    # (the original Shadow swordswoman-partner persona).
    persona_config_json: str = ""

    # ---- Long-task knobs ----
    # Maximum LLM ↔ tool round-trips per chat turn. The old hard-coded value
    # of 6 was tight for multi-step coding tasks (read repo → install dep →
    # run tests → fix → re-run). Bump to 12 by default; the UI can lower it
    # for cost-sensitive setups.
    max_tool_iterations: int = 12
    # When enabled, the agent emits a structured plan ("I'll do X, then Y…")
    # *before* it starts calling tools, so the UI can render a checklist that
    # ticks off steps as they complete. Recommend keeping on; agents can opt
    # out per turn if the user request is a one-line ask.
    enable_plan_first: bool = True
    # Cap stdout/stderr forwarding from streaming subprocess to prevent a
    # runaway command from drowning the SSE stream. Counted in characters.
    terminal_stream_max_chars: int = 8000

    # ---- Routing cost control ----
    # When the local keyword router matches a high-specificity, near-
    # unambiguous keyword (e.g. ``pytest``/``截图``/``人设``), skip the LLM
    # second-opinion (``router.plan``) and save one model call per turn.
    # Disable to always run the LLM router (more robust on edge phrasings,
    # but ~2x routing cost). Multi-intent / vague messages are never treated
    # as decisive, so they still get the LLM second opinion.
    router_skip_plan_when_decisive: bool = True

    # ---- Workspace permission broker ----
    # When the agent needs to access a directory outside the project workspace,
    # the broker surfaces a ``permission_request`` SSE event and awaits a user
    # decision instead of failing outright. ``workspace_allowlist`` and
    # ``workspace_denylist`` are JSON arrays of absolute paths (or ``~``
    # paths) — the broker matches against them as path prefixes.
    workspace_allowlist_json: str = "[]"
    workspace_denylist_json: str = (
        # Sensible Windows defaults — these are *never* unlockable, even if a
        # user accidentally tries to add them to the allowlist. The deny list
        # always wins.
        '["C:\\\\Windows", "C:\\\\Program Files", "C:\\\\Program Files (x86)", '
        '"~/.ssh", "~/.aws", "~/.docker"]'
    )
    require_path_confirmation: bool = True
    permission_request_timeout_seconds: int = 60

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # Validate values on direct attribute assignment so a bad
        # /api/settings PUT (e.g. provider='not-a-provider') is rejected
        # instead of being silently stored.
        validate_assignment=True,
    )

    @staticmethod
    def _clean_base(url: str) -> str:
        return url.rstrip("/")

    def resolved_model(self, provider: Provider | None = None) -> str:
        selected = provider or self.provider
        per_provider = {
            "openai": self.openai_model,
            "anthropic": self.anthropic_model,
            "vllm": self.vllm_model,
            "minimax": self.minimax_model,
            "modelscope": self.modelscope_model,
        }
        return per_provider.get(selected) or self.model

    def resolved_api_key(self, provider: Provider | None = None) -> str:
        selected = provider or self.provider
        per_provider = {
            "openai": self.openai_oauth_token or self.openai_api_key,
            "anthropic": self.anthropic_api_key,
            "vllm": self.vllm_api_key,
            "minimax": self.minimax_api_key,
            "modelscope": self.modelscope_api_key,
        }
        return per_provider.get(selected) or self.api_key

    def resolved_api_base(self, provider: Provider | None = None) -> str:
        selected = provider or self.provider
        per_provider = {
            "openai": self.openai_api_base,
            "anthropic": self.anthropic_api_base,
            "vllm": self.vllm_api_base or self.api_base,
            "minimax": self.minimax_api_base,
            "modelscope": self.modelscope_api_base,
        }
        return self._clean_base(per_provider.get(selected) or self.api_base)

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
