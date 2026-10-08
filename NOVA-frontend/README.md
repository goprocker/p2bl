# NOVA — frontend

The chat UI of **NOVA**, a fully local AI agent. React + Vite, styled like a
modern assistant (ChatGPT-style layout) with a persistent sidebar, live
streaming, and full voice support.

## Features

- **Live streaming** — answers render token-by-token (SSE), with real-time
  tool chips ("🔧 get_weather · running…") and a stop button
- **Conversation history** — sidebar lists past chats; click to reopen and
  continue any of them
- **Voice** — tap-to-talk 🎤 and hands-free 🎙️ mode with in-browser voice
  activity detection (adaptive noise floor, pre-roll capture, echo-loop
  protection); replies are spoken aloud
- **Slash commands** — type `/model` to switch the AI model from the input,
  Claude-CLI style, with keyboard navigation
- **Prompt history** — ↑/↓ recalls previous prompts like a terminal
- **Health status** — live indicator of backend, database, and model

## Quick start

Requires the [NOVA backend](https://github.com/ReeganKumaran/NOVA-backend)
running on port 8000.

```bash
npm install
npm run dev        # → http://localhost:5173
```

Point at a different backend with an env var:

```bash
VITE_API_URL=http://other-host:8000 npm run dev
```

## Structure

```
src/
  App.jsx                 state, streaming/voice/history API calls
  components/
    ChatBox.jsx           messages, input, palette, mic controls
    MessageBubble.jsx     bubbles, tool chips, lightweight rich text
  lib/
    handsfree.js          voice-activity detection + WAV encoding (testable)
  styles.css              dark theme, sidebar layout
```

Built by [Reegan](https://reeganlabs.com).
