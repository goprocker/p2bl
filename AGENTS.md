# Repository Guidelines

## Project Structure & Module Organization

This repository contains frontend and backend components. Run Git commands from the repository root.

- `frontend/`: React 18 and Vite chat UI. `src/App.jsx` coordinates API calls and state; `src/components/` contains UI components; `src/lib/handsfree.js` handles voice detection and WAV encoding. Styling lives in `src/styles.css`.
- `backend/`: FastAPI service. `app/main.py` defines endpoints; `app/models/` defines schemas; `app/agent/` owns orchestration and tool registration; `app/tools/`, `app/services/`, and `app/db/` isolate integrations and persistence.
- Backend voice assets belong in ignored `voices/`; vector data belongs in ignored `chroma_data/`.

## Build, Test, and Development Commands

From `frontend/`:

- `npm install`: install dependencies.
- `npm run dev`: start Vite at `http://localhost:5173`.
- `npm run build`: produce production output in `dist/`.
- `npm run preview`: preview the production build.

From `backend/`, use Python 3.11+ and a virtual environment:

- `pip install -r requirements.txt`: install backend dependencies.
- `playwright install chromium`: install the browser used by tools.
- `uvicorn app.main:app --reload --port 8000`: start the API.

Copy `.env.example` to `.env`. Start MongoDB and Ollama, and install the configured models; follow the backend README for voice setup.

## Coding Style & Naming Conventions

Match existing code: JavaScript uses two-space indentation, single quotes, and no semicolons. Use PascalCase for React components and camelCase for functions. Python uses four-space indentation, snake_case functions/modules, and type annotations. No formatter or linter configuration is checked in. Register new tools in `app/agent/tool_registry.py`.

## Testing Guidelines

No automated test suite, test script, or coverage threshold is configured. Build the frontend and check backend `/health` and `/docs`. Exercise affected chat streaming, cancellation, history, memory, or voice flows. Document reproduction steps and results in the PR; define naming and execution commands when introducing tests.

## Commit & Pull Request Guidelines

Existing history uses short descriptive subjects. For Codex commits, use Conventional Commits, a change-specific explanatory body, and blank lines separating subject, body, and this trailer:

`Co-authored-by: Jarvis <technicalmanjash@gmail.com>`

Never add Claude attribution or fabricated session URLs. PRs should explain behavior changes, link relevant issues, record validation, and include screenshots for UI changes.

## Security & Configuration

Never commit `.env`, credentials, personal memories, or downloaded models. Configure backend settings through `app/config.py`; use frontend `VITE_API_URL` for alternate API hosts. Browser-exposed Vite variables must not contain secrets.
