# P2BL

A smart-home assistant with a React chat interface, a rule-based Automation Lab, and a Python API. Explore lighting, energy saving, comfort, and safety scenarios, inspect automation decisions, and chat with an AI assistant.

## Features

- Automation Lab with simulated sensor inputs and explanations of selected actions.
- Rule priorities, resident overrides, stale-observation checks, and occupancy privacy controls.
- Streaming AI chat, conversation history, model selection, and optional voice input/output.
- Full backend with MongoDB persistence, ChromaDB memory, Ollama models, optional Groq chat, and external tools.
- Lightweight Groq demo API for chat without the full database and local-model stack.

## Repository layout

```text
frontend/   React 18 + Vite interface
backend/    FastAPI service, automation engine, tools, and tests
```

## Quick start: classroom demo

Install Node.js, Python 3.11+, and obtain a Groq API key for AI chat. Run these commands from the repository root in PowerShell:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install fastapi uvicorn httpx
Copy-Item .env.example .env
```

Set `GROQ_API_KEY` in `backend/.env`, then start the demo API:

```powershell
uvicorn demo_api:app --reload --port 8000
```

In another terminal, start the interface:

```powershell
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. The Automation Lab runs in the browser; demo AI chat requires the Groq key and sends chat messages to Groq. The demo API does not provide persistent history, memory, voice, or live device control.

## Full backend

From `backend/`, activate the virtual environment, install the complete dependencies, and install Chromium:

```powershell
pip install -r requirements.txt
playwright install chromium
```

Start MongoDB and Ollama. If Docker is installed, MongoDB can be started with:

```powershell
docker run -d --name p2bl-mongo -p 27017:27017 -v p2bl_mongo:/data/db mongo:7
ollama pull qwen3
ollama pull nomic-embed-text
```

Configure `backend/.env`, then run:

```powershell
uvicorn app.main:app --reload --port 8000
```

Use either the demo API or full API on port 8000 at a time. The full backend prefers Groq when `GROQ_API_KEY` is configured; leave it empty to use Ollama for chat. Voice requires Whisper and a configured Piper voice; see [backend setup](backend/README.md).

Health endpoint: http://localhost:8000/health. Interactive API docs: http://localhost:8000/docs.

For a different API address, set `VITE_API_URL` in `frontend/.env` and restart Vite. Vite variables are public; never put API keys there.

## Automation and device control

Automation starts in dry-run mode. Live execution requires the external ProjectMyRoom API, `MYROOM_API_URL`, `MYROOM_API_TOKEN`, and `AUTOMATION_LIVE_ENABLED=true`, as well as a live execution request. The external device service is not included in this repository.

## Validation

```powershell
cd frontend
npm run build
cd ../backend
python -m unittest discover -s tests -v
```

## Configuration and privacy

Keep credentials in `backend/.env`. Environment files, dependencies, build output, model assets, logs, and vector data are excluded from Git. Local models support local processing, but Groq and web/weather tools communicate with external services. This development API should run on a trusted local network.

## Credits

Based on the frontend and backend originally built by [Reegan](https://reeganlabs.com), with P2BL smart-home automation and demo additions.
