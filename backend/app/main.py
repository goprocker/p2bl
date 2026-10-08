"""FastAPI entrypoint.

Run with:  uvicorn app.main:app --reload --port 8000  (from backend/)

Endpoints:
  POST   /chat          - talk to the agent (text in, text out)
  POST   /chat/stream   - same, but streams live progress (SSE: tokens, tool events)
  POST   /voice/chat    - talk to the agent (audio in, audio + text out)
  POST   /voice/speak   - synthesize any text to speech
  GET    /health        - liveness of Mongo + Ollama + voice stack
  GET    /memories      - list saved memories
  POST   /memory        - save a memory directly
  DELETE /memory/{id}   - delete a memory
  POST   /documents     - upload a text document into memory
"""

import base64
import json
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from app.agent.agent_loop import run_agent, run_agent_stream
from app import automation_service
from app.config import settings, setup_logging
from app.db import chroma, mongo
from app.models.schemas import (
    ChatMessageOut,
    ChatRequest,
    ChatResponse,
    ConversationOut,
    DocumentOut,
    HealthResponse,
    MemoryCreate,
    MemoryOut,
    SpeakRequest,
    SpeakResponse,
    VoiceChatResponse,
)
from app.models.schemas import AutomationEvaluateRequest, RuleToggleRequest
from app.services import ollama_service, voice_service
from app.tools import browser_tool, memory_tool

setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    logger.info("Starting local AI agent (provider=%s, model=%s)",
                settings.llm_provider, settings.chat_model)
    if not await mongo.ping():
        logger.warning("MongoDB is not reachable — start it with: docker compose up -d mongo")
    if not await ollama_service.ping():
        logger.warning("Ollama is not reachable — start it with: ollama serve")
    yield
    await browser_tool.shutdown()
    await mongo.close()
    logger.info("Shutdown complete")


app = FastAPI(title="Local AI Agent", version="0.1.0", lifespan=lifespan)

# CORS_ORIGINS=* opens the API to any origin; a regex echo is required
# because allow_origins=["*"] + credentials is rejected by browsers.
_cors_kwargs = (
    {"allow_origin_regex": ".*"}
    if "*" in settings.cors_origin_list
    else {"allow_origins": settings.cors_origin_list}
)
app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    **_cors_kwargs,
)


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    try:
        result = await run_agent(
            request.message, request.user_id, request.conversation_id, request.model
        )
    except Exception as exc:  # noqa: BLE001 — return a clean 502 instead of a stack trace
        logger.exception("Agent run failed")
        raise HTTPException(
            status_code=502,
            detail=f"Agent failed: {exc}. Is Ollama running and the model pulled?",
        ) from exc

    return ChatResponse(
        answer=result.answer,
        conversation_id=result.conversation_id,
        tool_calls=result.tool_calls,
        memories_used=result.memories_used,
    )


@app.post("/chat/stream")
async def chat_stream(request: ChatRequest) -> StreamingResponse:
    """Live-streaming chat: Server-Sent Events with token/tool_*/done events."""

    async def event_source():
        try:
            async for event in run_agent_stream(
                request.message, request.user_id, request.conversation_id, request.model
            ):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as exc:  # noqa: BLE001 — stream the error to the UI
            logger.exception("Streaming agent run failed")
            detail = f"Agent failed: {exc}. Is Ollama running and the model pulled?"
            yield f"data: {json.dumps({'type': 'error', 'detail': detail})}\n\n"

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/automation/rules")
async def automation_rules() -> list[dict]:
    return await automation_service.rules()


@app.patch("/automation/rules/{rule_id}")
async def update_automation_rule(rule_id: str, request: RuleToggleRequest) -> dict:
    rule = await automation_service.set_rule_enabled(rule_id, request.enabled)
    if rule is None:
        raise HTTPException(status_code=404, detail="Automation rule not found")
    return rule


@app.post("/automation/evaluate")
async def evaluate_automation(request: AutomationEvaluateRequest) -> dict:
    return await automation_service.run(request.model_dump(mode="json", exclude_none=True))


@app.get("/automation/decisions")
async def automation_decisions(limit: int = Query(default=20, ge=1, le=100)) -> list[dict]:
    return await automation_service.decisions(limit)


@app.post("/voice/chat", response_model=VoiceChatResponse)
async def voice_chat(
    audio: UploadFile = File(...),
    user_id: str = Form(default="default_user"),
    conversation_id: Optional[str] = Form(default=None),
    model: Optional[str] = Form(default=None),
) -> VoiceChatResponse:
    """Full voice loop: audio in → transcribe → agent → synthesize → audio out."""
    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Empty audio upload")

    # Feed the user's saved memories to Whisper as a vocabulary hint so
    # personal names (projects, people) transcribe correctly instead of
    # being "corrected" into similar-sounding common words.
    vocab_hint = None
    try:
        memories = await mongo.list_memories(user_id, limit=12)
        joined = " ".join(m["content"] for m in memories)
        vocab_hint = joined[:500] or None
    except Exception:  # noqa: BLE001 — the hint is optional
        logger.warning("Could not build Whisper vocabulary hint", exc_info=True)

    try:
        transcript = await voice_service.transcribe(audio_bytes, initial_prompt=vocab_hint)
    except Exception as exc:  # noqa: BLE001 — surface a clean error to the UI
        logger.exception("Transcription failed")
        raise HTTPException(status_code=502, detail=f"Transcription failed: {exc}") from exc

    if not transcript:
        raise HTTPException(
            status_code=422,
            detail="Could not hear any speech in the recording. Try speaking closer to the mic.",
        )

    try:
        result = await run_agent(transcript, user_id, conversation_id, model)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Agent run failed")
        raise HTTPException(
            status_code=502,
            detail=f"Agent failed: {exc}. Is Ollama running and the model pulled?",
        ) from exc

    try:
        wav_bytes, engine = await voice_service.synthesize(result.answer)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Speech synthesis failed")
        raise HTTPException(status_code=502, detail=f"Speech synthesis failed: {exc}") from exc

    return VoiceChatResponse(
        transcript=transcript,
        answer=result.answer,
        conversation_id=result.conversation_id,
        tool_calls=result.tool_calls,
        memories_used=result.memories_used,
        audio_base64=base64.b64encode(wav_bytes).decode("ascii"),
        tts_engine=engine,
    )


@app.post("/voice/speak", response_model=SpeakResponse)
async def voice_speak(request: SpeakRequest) -> SpeakResponse:
    """Synthesize arbitrary text to speech (used by the UI's 'speak replies' toggle)."""
    try:
        wav_bytes, engine = await voice_service.synthesize(request.text)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Speech synthesis failed")
        raise HTTPException(status_code=502, detail=f"Speech synthesis failed: {exc}") from exc
    return SpeakResponse(
        audio_base64=base64.b64encode(wav_bytes).decode("ascii"),
        tts_engine=engine,
    )


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    mongo_ok = await mongo.ping()
    ollama_ok = await ollama_service.ping()
    stt_ok, stt_detail = voice_service.stt_ready()
    return HealthResponse(
        status="ok" if (mongo_ok and ollama_ok) else "degraded",
        mongo="up" if mongo_ok else "down",
        ollama="up" if ollama_ok else "down",
        model=settings.chat_model,
        voice_stt=stt_detail if stt_ok else "unavailable",
        voice_tts=settings.tts_engine,
        llm_provider=settings.llm_provider,
        llm="up" if ollama_ok else "down",
        automation_mode="live-capable" if settings.automation_live_enabled else "dry-run",
        smart_room_configured=bool(settings.myroom_api_token),
    )


@app.get("/models")
async def list_models() -> dict:
    """Chat models available on the Ollama host, for the UI's model picker."""
    try:
        models = await ollama_service.list_models()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Could not list models: {exc}") from exc
    return {"models": models, "default": settings.chat_model,
            "provider": settings.llm_provider}


@app.get("/conversations", response_model=list[ConversationOut])
async def list_conversations(
    user_id: str = Query(default="default_user"), limit: int = Query(default=50, le=200)
) -> list[ConversationOut]:
    """Chat history: past conversations, most recently active first."""
    return [ConversationOut(**c) for c in await mongo.list_conversations(user_id, limit)]


@app.get("/conversations/{conversation_id}/messages", response_model=list[ChatMessageOut])
async def get_conversation_messages(conversation_id: str) -> list[ChatMessageOut]:
    """All messages of one conversation, oldest first (to reopen it in the UI)."""
    if not await mongo.conversation_exists(conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    return [ChatMessageOut(**m) for m in await mongo.get_all_messages(conversation_id)]


@app.get("/vectors")
async def inspect_vectors(
    collection: str = Query(default="memories"),
    limit: int = Query(default=100, le=500),
) -> dict:
    """Read-only peek into the ChromaDB vector store (Compass-style browsing).

    Shows each stored item's text, metadata, and a preview of its embedding.
    collection = "memories" or "documents".
    """
    if collection not in (chroma.MEMORIES, chroma.DOCUMENTS):
        raise HTTPException(
            status_code=400,
            detail=f"collection must be '{chroma.MEMORIES}' or '{chroma.DOCUMENTS}'",
        )
    result = await chroma.dump(collection, limit)
    return {"collection": collection, **result}


@app.get("/memories", response_model=list[MemoryOut])
async def list_memories(user_id: str = Query(default="default_user")) -> list[MemoryOut]:
    return [MemoryOut(**m) for m in await mongo.list_memories(user_id)]


@app.post("/memory", response_model=MemoryOut, status_code=201)
async def create_memory(memory: MemoryCreate) -> MemoryOut:
    await mongo.ensure_user(memory.user_id)
    await memory_tool.save_memory(
        content=memory.content,
        type=memory.type,
        importance=memory.importance,
        user_id=memory.user_id,
        source=memory.source,
    )
    saved = await mongo.list_memories(memory.user_id, limit=1)
    return MemoryOut(**saved[0])


@app.delete("/memory/{memory_id}", status_code=204)
async def remove_memory(memory_id: str) -> None:
    if not await memory_tool.delete_memory(memory_id):
        raise HTTPException(status_code=404, detail="Memory not found")


@app.post("/documents", response_model=DocumentOut, status_code=201)
async def upload_document(
    file: UploadFile = File(...), user_id: str = Query(default="default_user")
) -> DocumentOut:
    """Upload a text-based file (txt/md/csv/...) into searchable memory."""
    raw = await file.read()
    text = raw.decode("utf-8", errors="ignore").strip()
    if not text:
        raise HTTPException(status_code=400, detail="File is empty or not readable as text")

    chunks = _chunk_text(text)
    doc_id = await mongo.insert_document(user_id, file.filename or "upload.txt", chunks)
    await chroma.add_batch(
        chroma.DOCUMENTS,
        ids=[f"{doc_id}:{i}" for i in range(len(chunks))],
        contents=chunks,
        metadatas=[
            {"user_id": user_id, "doc_id": doc_id, "chunk": i,
             "filename": file.filename or "upload.txt"}
            for i in range(len(chunks))
        ],
    )
    logger.info("Indexed document %s (%d chunks)", file.filename, len(chunks))
    return DocumentOut(
        id=doc_id,
        filename=file.filename or "upload.txt",
        chunks=len(chunks),
        message="Document stored. Ask about it in chat — the agent will find it via search_memory.",
    )


def _chunk_text(text: str, size: int = 1_000, overlap: int = 150) -> list[str]:
    """Simple sliding-window chunking, good enough for local semantic search."""
    chunks = []
    start = 0
    while start < len(text):
        chunks.append(text[start : start + size])
        start += size - overlap
    return chunks
