# P2BL — backend

The backend of **P2BL**, a fully local AI agent (not just a chatbot): it
decides when to use tools, fetches live data with a headless browser and
APIs, stores long-term memory in MongoDB, recalls it semantically with
vector search, and talks — speech-to-text and text-to-speech — all powered
by local models through Ollama. Optional Groq chat and web/weather tools
send requests to external services; see the [root README](../README.md).

```
User message / voice
      │
      ▼
FastAPI ──► agent loop ──► Ollama (local LLM)
                │               │ "I need a tool"
                ▼               ▼
          7 tools: web search · read page · weather ·
          save/recall memory · summarize · date-time
                │
                ▼
   MongoDB (records) + ChromaDB (meaning vectors)
```

## Features

- **Agent loop** with tool calling, streamed live over SSE (tokens + tool events)
- **Long-term memory** — MongoDB as source of truth, ChromaDB for semantic
  recall, automatic supersede when a fact is updated
- **Voice** — Whisper (STT) and Piper (TTS), fully local, with a
  memory-driven vocabulary hint so personal names transcribe correctly
- **Live web access** — layered search (HTTP-first with headless-browser
  fallbacks) and Playwright page reading
- **Model picker** — any tool-calling model on your Ollama host, switchable
  per request

## Quick start

Prerequisites: Python 3.11+, Docker (for MongoDB), [Ollama](https://ollama.com).

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

cp .env.example .env          # defaults work out of the box

# MongoDB
docker run -d --name p2bl-mongo -p 27017:27017 -v p2bl_mongo:/data/db mongo:7

# Models
ollama pull qwen3            # or any tool-calling model — set OLLAMA_MODEL
ollama pull nomic-embed-text  # embeddings for vector memory

# Piper voice (TTS) — or set TTS_ENGINE=say on macOS to skip
mkdir -p voices && cd voices
curl -sLO "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx"
curl -sLO "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json"
cd ..

uvicorn app.main:app --reload --port 8000
```

Check: `curl http://localhost:8000/health` — interactive docs at
http://localhost:8000/docs

## API

| Endpoint | Purpose |
|---|---|
| `POST /chat` | talk to the agent (text in, text out) |
| `POST /chat/stream` | same, streamed as SSE (tokens, tool events) |
| `POST /voice/chat` | audio in → transcript + answer + spoken reply |
| `POST /voice/speak` | text → speech |
| `GET /models` | models available on the Ollama host |
| `GET /conversations` · `/conversations/{id}/messages` | chat history |
| `GET /memories` · `POST /memory` · `DELETE /memory/{id}` | memory CRUD |
| `POST /documents` | index a text file into searchable memory |
| `GET /vectors` | inspect the ChromaDB vector store |
| `GET /health` | liveness of Mongo, Ollama and the voice stack |

## Project structure

```
app/
  main.py               FastAPI app + endpoints
  config.py             env-based settings (.env)
  agent/
    agent_loop.py       reasoning ↔ tool-execution loop (event stream)
    prompts.py          system prompt: rules, memories, freshness
    tool_registry.py    tool declarations + safe executor — add tools here
  tools/                one file per tool
  db/
    mongo.py            MongoDB collections (Motor, async)
    chroma.py           embedded ChromaDB + Ollama embeddings
  services/
    ollama_service.py   chat / stream / embed / model list
    voice_service.py    Whisper STT + Piper TTS (macOS `say` fallback)
```

## Adding a tool

Write an async function in `app/tools/` that returns a string, then add one
entry to the `TOOLS` dict in `app/agent/tool_registry.py`. The agent loop
offers every registered tool to the LLM automatically, logs every call, and
handles timeouts and failures for you.

---

Frontend (React chat UI with streaming, voice and hands-free mode) lives in
[frontend directory](../frontend). Built by [Reegan](https://reeganlabs.com).
