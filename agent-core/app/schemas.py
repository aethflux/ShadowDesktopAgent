from typing import Any, Literal

from pydantic import BaseModel, Field


class ChatAttachment(BaseModel):
    kind: Literal["image"] = "image"
    path: str | None = None
    mime_type: str | None = None
    data_url: str | None = None


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"] = "user"
    content: str


class ChatRequest(BaseModel):
    message: str
    attachments: list[ChatAttachment] = Field(default_factory=list)
    session_id: str = "default"


class ObservationRequest(BaseModel):
    session_id: str = "pet-screen-session"
    trigger: Literal["manual", "interval"] = "manual"
    focus: str | None = None
    # Engagement telemetry from the desktop client (optional).
    keypresses_last_minute: int = 0
    mouse_moves_last_minute: int = 0
    is_idle: bool = False


class ToolCallRecord(BaseModel):
    name: str
    args: dict[str, Any] = Field(default_factory=dict)
    result: str
    # ``success`` is the canonical signal for whether the tool succeeded.
    # Defaults to True so existing call sites that don't set it (e.g. screen
    # capture) keep their pre-existing semantics.
    success: bool = True
    # Optional progress-tracking metadata. Populated by ``LLMAgent`` when a
    # ``progress_cb`` is wired up by the orchestrator so the streaming UI can
    # correlate ``tool_start`` / ``tool_end`` events with the final record.
    step_id: str | None = None
    duration_ms: int | None = None


class AgentTrace(BaseModel):
    active_agent: str
    delegated_to: str | None = None
    reasoning: str
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)


class IntentMatch(BaseModel):
    intent: Literal[
        "conversation",
        "screen_observation",
        "continuous_companion",
        "terminal",
        "coding",
        "memory_profile",
        "persona",
        "voice",
        "unknown",
    ] = "unknown"
    delegated_to: Literal["companion-agent", "desktop-agent", "terminal-agent"] = "companion-agent"
    confidence: float = 0.0
    reasoning: str = ""
    signals: list[str] = Field(default_factory=list)
    tool_candidates: list[str] = Field(default_factory=list)


class ChatResponse(BaseModel):
    reply: str
    trace: AgentTrace
    memory_summary: str
    task: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)


class ObservationResponse(ChatResponse):
    should_speak: bool = True
    significance: Literal["low", "medium", "high"] = "medium"


class VoiceTTSRequest(BaseModel):
    text: str
    voice: str | None = None
    rate: float = 1.0
    pitch: float = 1.05


class VoiceTTSResponse(BaseModel):
    text: str
    engine: Literal["browser-speech", "edge", "modelscope", "minimax", "gemini"] = "browser-speech"
    voice: str | None = None
    rate: float = 1.0
    pitch: float = 1.05
    audio_url: str | None = None  # set when cloud TTS generated audio


class MemoryItem(BaseModel):
    session_id: str
    role: str
    content: str
    tags: list[str] = Field(default_factory=list)


class UserProfile(BaseModel):
    session_id: str
    display_name: str | None = None
    preferred_name: str | None = None
    role: str | None = None
    company: str | None = None
    location: str | None = None
    bio: str | None = None
    goals: list[str] = Field(default_factory=list)
    preferences: list[str] = Field(default_factory=list)
    projects: list[str] = Field(default_factory=list)
    relationship_notes: list[str] = Field(default_factory=list)
    notable_memories: list[str] = Field(default_factory=list)
    last_user_messages: list[str] = Field(default_factory=list)
    interaction_count: int = 0


class ObservationState(BaseModel):
    session_id: str
    last_comment: str | None = None
    last_topic: str | None = None
    last_screen_hash: str | None = None
    observation_count: int = 0


class ToolSpec(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any]


class TerminalSessionState(BaseModel):
    session_id: str
    cwd: str
    history: list[str] = Field(default_factory=list)


class Skill(BaseModel):
    """A loaded skill with parsed frontmatter and prompt content."""
    name: str
    description: str
    triggers: list[str] = Field(default_factory=list)
    prompt: str = ""          # content after the YAML frontmatter
    dir_path: str = ""         # absolute path to the skill directory


class ProviderInfo(BaseModel):
    """Status of one LLM provider — used by the settings UI to show which
    provider is configured / available."""
    id: str
    display_name: str
    configured: bool          # an API key is present
    default_model: str
    supports_vision: bool


# ---- Permission broker ---------------------------------------------------- #


class PermissionRequest(BaseModel):
    """An outstanding ask from the agent to access a directory the user has
    not pre-approved. Carries enough context for the UI to render a useful
    dialog ("terminal.run wants to use cwd=…") before the user decides."""
    request_id: str
    path: str
    reason: str
    tool_name: str | None = None
    session_id: str | None = None


class PermissionDecision(BaseModel):
    """User's response to a ``PermissionRequest``. The four choices map to
    different lifetimes:
    - ``allow_once``  — only this single tool call.
    - ``allow_session`` — every subsequent call in this chat session.
    - ``allow_always`` — written into ``workspace_allowlist`` permanently.
    - ``deny`` — abort the current tool call.
    """
    request_id: str
    decision: Literal["allow_once", "allow_session", "allow_always", "deny"]


# ---- Persona / personality ----------------------------------------------- #


class PersonaConfig(BaseModel):
    """User-tunable personality knobs.

    Stored as a single JSON blob in ``settings.persona_config_json`` so the
    whole structure can be swapped atomically (e.g. when the user clicks
    "apply preset"). All fields have sensible defaults — an empty config is
    valid and renders to the original Hoshino swordswoman-partner persona.
    """
    name: str = "星野"
    archetype: str = "swordswoman_partner"
    personality_traits: list[str] = Field(
        default_factory=lambda: ["温柔", "坚定", "略带俏皮", "保护欲强"],
    )
    speaking_style: str = "简洁有力，温暖有节制"
    address_user_as: str = "你"
    backstory: str = ""
    forbidden_topics: list[str] = Field(default_factory=list)
    catchphrases: list[str] = Field(default_factory=list)
    emoji_usage: Literal["none", "occasional", "frequent"] = "occasional"
    response_length: Literal["concise", "balanced", "detailed"] = "balanced"
    # Optional escape hatch for power users — when non-empty this is appended
    # verbatim to the rendered system prompt, overriding nothing but adding
    # arbitrary extra instructions.
    custom_system_prompt: str = ""


class PersonaPreset(BaseModel):
    """One named preset surfaced via ``GET /api/persona/presets``. The UI
    shows ``label`` / ``description``; clicking applies ``config`` to the
    settings overlay."""
    id: str
    label: str
    description: str
    config: PersonaConfig


class SettingsView(BaseModel):
    """Read-only public view of mutable settings, with light context.

    Secrets (api_key etc.) are deliberately absent — they live in ``.env``
    and never round-trip through the UI.
    """
    # LLM
    provider: str
    model: str
    vision_provider: str
    vision_model: str
    enable_prompt_cache: bool
    openai_model: str | None = None
    anthropic_model: str | None = None
    vllm_model: str | None = None
    minimax_model: str | None = None
    modelscope_model: str | None = None
    # Reliability
    model_max_retries: int
    model_retry_backoff_seconds: float
    anthropic_max_tokens: int
    # Memory
    enable_semantic_memory: bool
    semantic_top_k: int
    # Voice
    enable_edge_tts: bool
    edge_tts_voice: str
    edge_tts_rate: str
    edge_tts_pitch: str
    enable_minimax_voice: bool
    minimax_tts_voice_id: str
    minimax_tts_speed: float
    minimax_tts_pitch: float
    enable_modelscope_tts: bool
    enable_gemini_tts: bool
    gemini_tts_voice: str
    # Behavior
    rate_limit_capacity: int
    rate_limit_refill_per_second: float
    tts_audio_retention_hours: int
    enable_gui_automation: bool
    # Permission broker (workspace_*_json are JSON arrays of paths)
    workspace_allowlist_json: str
    workspace_denylist_json: str
    require_path_confirmation: bool
    permission_request_timeout_seconds: int
    # Persona — full PersonaConfig encoded as JSON
    persona_config_json: str


class SettingsPatch(BaseModel):
    """Optional update mask. Any field omitted is left unchanged."""
    # LLM
    provider: str | None = None
    model: str | None = None
    vision_provider: str | None = None
    vision_model: str | None = None
    enable_prompt_cache: bool | None = None
    openai_model: str | None = None
    anthropic_model: str | None = None
    vllm_model: str | None = None
    minimax_model: str | None = None
    modelscope_model: str | None = None
    # Reliability
    model_max_retries: int | None = None
    model_retry_backoff_seconds: float | None = None
    anthropic_max_tokens: int | None = None
    # Memory
    enable_semantic_memory: bool | None = None
    semantic_top_k: int | None = None
    # Voice
    enable_edge_tts: bool | None = None
    edge_tts_voice: str | None = None
    edge_tts_rate: str | None = None
    edge_tts_pitch: str | None = None
    enable_minimax_voice: bool | None = None
    minimax_tts_voice_id: str | None = None
    minimax_tts_speed: float | None = None
    minimax_tts_pitch: float | None = None
    enable_modelscope_tts: bool | None = None
    enable_gemini_tts: bool | None = None
    gemini_tts_voice: str | None = None
    # Behavior
    rate_limit_capacity: int | None = None
    rate_limit_refill_per_second: float | None = None
    tts_audio_retention_hours: int | None = None
    enable_gui_automation: bool | None = None
    # Permission broker
    workspace_allowlist_json: str | None = None
    workspace_denylist_json: str | None = None
    require_path_confirmation: bool | None = None
    permission_request_timeout_seconds: int | None = None
    # Persona
    persona_config_json: str | None = None
