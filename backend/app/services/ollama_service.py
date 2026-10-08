"""Chat-provider adapter for Groq or local Ollama.

Everything that talks to an LLM goes through this module:
- chat()   : tool-calling chat completions for the agent loop
- complete(): plain one-shot completions (used by summarize_text)
- embed()  : embeddings for vector memory
"""

import json
import logging
import re
from typing import Any, Optional

import httpx

try:
    from ollama import AsyncClient
except ImportError:  # Groq chat can run without the optional local client
    AsyncClient = None  # type: ignore[assignment,misc]

from app.config import settings

logger = logging.getLogger(__name__)

_client: Optional[Any] = None
_GROQ_BASE = "https://api.groq.com/openai/v1"

# qwen3-style models may emit <think>...</think> blocks inside content
# on older Ollama versions; strip them so users never see raw reasoning.
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def get_client() -> AsyncClient:
    global _client
    if AsyncClient is None:
        raise RuntimeError("Ollama client is not installed")
    if _client is None:
        _client = AsyncClient(host=settings.ollama_host)
    return _client


def clean_content(text: str) -> str:
    return _THINK_RE.sub("", text or "").strip()


def _using_groq() -> bool:
    return bool(settings.groq_api_key)


def _groq_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.groq_api_key}",
            "Content-Type": "application/json"}


def _groq_messages(messages: list[dict]) -> list[dict]:
    """Remove Ollama-only fields while preserving OpenAI tool-call IDs."""
    normalized = []
    for original in messages:
        message = {key: value for key, value in original.items() if key != "tool_name"}
        if message.get("role") == "tool" and not message.get("tool_call_id"):
            raise ValueError("Groq tool result is missing tool_call_id")
        normalized.append(message)
    return normalized


def _groq_payload(messages: list[dict], tools: Optional[list[dict]],
                  model: Optional[str], stream: bool) -> dict:
    payload = {
        "model": model or settings.groq_model,
        "messages": _groq_messages(messages),
        "temperature": settings.ollama_temperature,
        "stream": stream,
    }
    if tools:
        payload.update({"tools": tools, "tool_choice": "auto"})
    return payload


async def chat(
    messages: list[dict], tools: Optional[list[dict]] = None, model: Optional[str] = None
) -> Any:
    """One chat turn against the local model, optionally with tools offered."""
    if _using_groq():
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{_GROQ_BASE}/chat/completions",
                headers=_groq_headers(),
                json=_groq_payload(messages, tools, model, False),
            )
            response.raise_for_status()
            return {"message": response.json()["choices"][0]["message"]}
    response = await get_client().chat(
        model=model or settings.ollama_model,
        messages=messages,
        tools=tools or [],
        options={"temperature": settings.ollama_temperature},
    )
    return response


async def chat_stream(
    messages: list[dict], tools: Optional[list[dict]] = None, model: Optional[str] = None
):
    """Like chat(), but yields response chunks as the model generates them."""
    if _using_groq():
        pending: dict[int, dict] = {}
        async with httpx.AsyncClient(timeout=60) as client:
            async with client.stream(
                "POST", f"{_GROQ_BASE}/chat/completions",
                headers=_groq_headers(),
                json=_groq_payload(messages, tools, model, True),
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data: ") or line == "data: [DONE]":
                        continue
                    event = json.loads(line[6:])
                    choices = event.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    content = delta.get("content") or ""
                    if content:
                        yield {"message": {"content": content}}
                    for fragment in delta.get("tool_calls") or []:
                        index = fragment.get("index", 0)
                        call = pending.setdefault(index, {
                            "id": "", "type": "function",
                            "function": {"name": "", "arguments": ""},
                        })
                        if fragment.get("id"):
                            call["id"] = fragment["id"]
                        function = fragment.get("function") or {}
                        call["function"]["name"] += function.get("name") or ""
                        call["function"]["arguments"] += function.get("arguments") or ""
        if pending:
            yield {"message": {"content": "", "tool_calls": list(pending.values())}}
        return

    stream = await get_client().chat(
        model=model or settings.ollama_model,
        messages=messages,
        tools=tools or [],
        stream=True,
        options={"temperature": settings.ollama_temperature},
    )
    async for chunk in stream:
        yield chunk


async def list_models() -> list[dict]:
    """Chat-capable models on the Ollama host, with tool-support flags."""
    if _using_groq():
        return [{"name": settings.groq_model, "supports_tools": True}]
    response = await get_client().list()
    models = []
    for entry in getattr(response, "models", None) or response.get("models", []):
        name = getattr(entry, "model", None) or entry.get("model", "")
        if not name or "embed" in name.lower():
            continue  # embedding models can't chat
        supports_tools = True
        try:
            info = await get_client().show(name)
            capabilities = getattr(info, "capabilities", None)
            if capabilities is not None:
                supports_tools = "tools" in capabilities
        except Exception:  # noqa: BLE001 — capability probe is best-effort
            pass
        models.append({"name": name, "supports_tools": supports_tools})
    return sorted(models, key=lambda m: m["name"])


async def complete(prompt: str, system: Optional[str] = None) -> str:
    """Plain completion without tools — used for summarization etc."""
    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    response = await chat(messages)
    return clean_content(response["message"]["content"])


async def embed(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts with the local embedding model."""
    response = await get_client().embed(model=settings.ollama_embed_model, input=texts)
    return list(response["embeddings"])


async def ping() -> bool:
    """True if the configured chat provider is reachable."""
    try:
        if _using_groq():
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(f"{_GROQ_BASE}/models", headers=_groq_headers())
                response.raise_for_status()
                return True
        await get_client().list()
        return True
    except Exception as exc:  # noqa: BLE001 — health check must never raise
        logger.warning("%s chat provider not reachable: %s", settings.llm_provider, exc)
        return False
