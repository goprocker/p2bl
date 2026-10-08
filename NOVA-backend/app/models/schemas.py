"""Pydantic schemas for API requests and responses."""

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


# ---------- /chat ----------

class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, description="The user's message")
    user_id: str = Field(default="default_user")
    conversation_id: Optional[str] = Field(
        default=None,
        description="Continue an existing conversation; omit to start a new one",
    )
    model: Optional[str] = Field(
        default=None,
        description="Override the default Ollama model for this request",
    )


class ToolCallInfo(BaseModel):
    tool: str
    arguments: dict[str, Any]
    result_preview: str
    success: bool
    duration_ms: int


class ChatResponse(BaseModel):
    answer: str
    conversation_id: str
    tool_calls: list[ToolCallInfo] = []
    memories_used: list[str] = []


# ---------- /conversations ----------

class ConversationOut(BaseModel):
    id: str
    title: str
    created_at: str
    last_message_at: str
    message_count: int


class ChatMessageOut(BaseModel):
    role: str
    content: str
    created_at: str


# ---------- /memory ----------

class MemoryCreate(BaseModel):
    content: str = Field(..., min_length=1)
    type: str = Field(default="fact", description="preference | fact | task | note | ...")
    importance: int = Field(default=5, ge=1, le=10)
    user_id: str = Field(default="default_user")
    source: str = Field(default="api")


class MemoryOut(BaseModel):
    id: str
    user_id: str
    type: str
    content: str
    importance: int
    source: str
    created_at: str
    updated_at: str


# ---------- /voice ----------

class VoiceChatResponse(BaseModel):
    transcript: str
    answer: str
    conversation_id: str
    tool_calls: list[ToolCallInfo] = []
    memories_used: list[str] = []
    audio_base64: str
    audio_mime: str = "audio/wav"
    tts_engine: str


class SpeakRequest(BaseModel):
    text: str = Field(..., min_length=1)


class SpeakResponse(BaseModel):
    audio_base64: str
    audio_mime: str = "audio/wav"
    tts_engine: str


# ---------- /health ----------

class HealthResponse(BaseModel):
    status: str
    mongo: str
    ollama: str
    model: str
    voice_stt: str
    voice_tts: str
    llm_provider: str = "ollama"
    llm: str = "down"
    automation_mode: str = "dry-run"
    smart_room_configured: bool = False


# ---------- /automation ----------

class AutomationObservations(BaseModel):
    occupied: Optional[bool] = None
    light_level_lux: Optional[float] = Field(default=None, ge=0, le=200_000)
    temperature_c: Optional[float] = Field(default=None, ge=-50, le=80)
    door_open: Optional[bool] = None
    away_mode: Optional[bool] = None
    home_power_w: Optional[float] = Field(default=None, ge=0)
    authorized_entry: Optional[bool] = None
    local_hour: Optional[int] = Field(default=None, ge=0, le=23)


class ResidentCommand(BaseModel):
    device: str = Field(..., min_length=1, max_length=100)
    state: Literal["on", "off"]
    hold_minutes: int = Field(default=30, ge=1, le=1_440)


class AutomationEvaluateRequest(BaseModel):
    room: str = Field(default="living_room", min_length=1, max_length=80)
    observed_at: Optional[datetime] = None
    observations: AutomationObservations
    occupancy_automation: bool = True
    approved_standby_devices: list[str] = []
    resident_command: Optional[ResidentCommand] = None
    dry_run: bool = True


class RuleToggleRequest(BaseModel):
    enabled: bool


# ---------- /documents ----------

class DocumentOut(BaseModel):
    id: str
    filename: str
    chunks: int
    message: str
