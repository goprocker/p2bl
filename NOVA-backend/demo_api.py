"""Lightweight Groq-backed API for the classroom demo.

Runs AI Chat without MongoDB, Ollama, Chroma, voice, or device dependencies.
The deterministic Automation Lab remains browser-local.
"""

import json
import os
from pathlib import Path
from uuid import uuid4

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field


def _load_env() -> None:
    path = Path(__file__).with_name(".env")
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        os.environ.setdefault(name.strip(), value.strip())


_load_env()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    user_id: str = "default_user"
    conversation_id: str | None = None
    model: str | None = None


app = FastAPI(title="NOVA Groq Demo API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict:
    configured = bool(GROQ_API_KEY)
    return {
        "status": "ok" if configured else "degraded",
        "mongo": "demo",
        "ollama": "unused",
        "llm": "configured" if configured else "missing key",
        "llm_provider": "groq",
        "model": GROQ_MODEL,
        "voice_stt": "disabled",
        "voice_tts": "disabled",
    }


@app.get("/models")
async def models() -> dict:
    return {"models": [{"name": GROQ_MODEL, "supports_tools": False}],
            "default": GROQ_MODEL, "provider": "groq"}


@app.get("/conversations")
async def conversations() -> list:
    return []


@app.get("/conversations/{conversation_id}/messages")
async def conversation_messages(conversation_id: str) -> list:
    return []


@app.post("/chat/stream")
async def chat_stream(request: ChatRequest) -> StreamingResponse:
    conversation_id = request.conversation_id or str(uuid4())

    async def events():
        if not GROQ_API_KEY:
            yield f"data: {json.dumps({'type': 'error', 'detail': 'GROQ_API_KEY is missing'})}\n\n"
            return
        payload = {
            "model": request.model or GROQ_MODEL,
            "messages": [
                {"role": "system", "content": (
                    "You are NOVA, an AI assistant demonstrating a rule-based "
                    "multi-agent smart-home research project. Keep answers concise, "
                    "clear, and suitable for explaining to a teacher. Describe only "
                    "the implemented demo: it uses mock sensor data, a Sensing Agent, "
                    "Context Agent, Rule Agent, Conflict Resolver, Device Agent, and "
                    "Notification Agent. The unusual-entry scenario means a door opens "
                    "while away mode is active and authorization is absent; the safety "
                    "policy selects an alert and suppresses the comfort-lighting action. "
                    "It is a dry run and does not operate physical locks, cameras, alarms, "
                    "or emergency services."
                    " When asked about home applications, explain the implemented "
                    "applications: evening lighting for an occupied low-light room, "
                    "away-mode energy reduction for resident-approved standby loads, "
                    "and unusual-entry safety notifications. Explain that temperature, "
                    "humidity, and appliance-state rules can be added through the same "
                    "agent pipeline, but are not currently simulated."
                    " Never invent sensors, numerical thresholds, room names, device "
                    "commands, or capabilities beyond these scenarios. The evening rule "
                    "uses occupied, low-light, and evening context. The energy rule uses "
                    "unoccupied, away-mode, high-power context and only approved standby "
                    "loads; its outcome is to switch off only those approved standby loads. "
                    "The safety rule uses door-open, away-mode, and unauthorized "
                    "entry context. For application questions, give a concise bullet list "
                    "of these three implemented applications and state that the result is "
                    "a local dry-run explanation, not a physical-device command."
                )},
                {"role": "user", "content": request.message},
            ],
            "temperature": 0.3,
            "stream": True,
        }
        answer_parts = []
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                async with client.stream(
                    "POST", GROQ_URL,
                    headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                    json=payload,
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.startswith("data: ") or line == "data: [DONE]":
                            continue
                        chunk = json.loads(line[6:])
                        choices = chunk.get("choices") or []
                        if not choices:
                            continue
                        text = (choices[0].get("delta") or {}).get("content") or ""
                        if text:
                            answer_parts.append(text)
                            yield f"data: {json.dumps({'type': 'token', 'text': text})}\n\n"
        except httpx.HTTPStatusError as exc:
            detail = f"Groq rejected the request (HTTP {exc.response.status_code}). Check key and model."
            yield f"data: {json.dumps({'type': 'error', 'detail': detail})}\n\n"
            return
        except Exception as exc:  # noqa: BLE001 - send clean demo error to UI
            yield f"data: {json.dumps({'type': 'error', 'detail': f'Groq connection failed: {exc}'})}\n\n"
            return
        yield f"data: {json.dumps({'type': 'done', 'answer': ''.join(answer_parts), 'conversation_id': conversation_id, 'tool_calls': [], 'memories_used': []})}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})
