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
