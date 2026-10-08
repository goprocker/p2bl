"""Tool registry — the single place where tools are declared.

To add a new tool:
  1. Write an async function in app/tools/ that returns a string.
  2. Add one entry to TOOLS below (handler + JSON schema).
That's it — the agent loop and the LLM pick it up automatically.
"""

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable

from app.config import settings
from app.tools import (
    browser_tool,
    datetime_tool,
    memory_tool,
    smartroom_tool,
    summarize_tool,
    weather_tool,
    webpage_tool,
)

logger = logging.getLogger(__name__)

ToolHandler = Callable[..., Awaitable[str]]


def _schema(name: str, description: str, properties: dict, required: list[str]) -> dict:
    """Build an Ollama/OpenAI-style function schema."""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


# handler: the coroutine to run
# needs_user: if True, the agent loop injects user_id (never exposed to the LLM)
TOOLS: dict[str, dict[str, Any]] = {
    "control_device": {
        "handler": smartroom_tool.control_device,
        "needs_user": False,
        "schema": _schema(
            "control_device",
            "Turn a REAL smart-room device ON or OFF (lights, fans, blinds). "
            "ALWAYS use this when the user asks to turn on/off, switch, enable "
            "or disable a light, fan, or other room device. To control every "
            "device at once pass device='all'. Never claim a device changed "
            "state unless this tool's result confirms it.",
            {
                "device": {
                    "type": "string",
                    "description": "Device name or type, e.g. 'bedroom light' or just "
                    "'light'. Use 'all' to control every device at once.",
                },
                "state": {
                    "type": "string",
                    "enum": ["on", "off"],
                    "description": "The desired state",
                },
            },
            ["device", "state"],
        ),
    },
    "list_room_devices": {
        "handler": smartroom_tool.list_room_devices,
        "needs_user": False,
        "schema": _schema(
            "list_room_devices",
            "List the smart-room devices and whether each is currently on or "
            "off. Use for questions like 'is the light on?' or 'what devices "
            "are in my room?'.",
            {},
            [],
        ),
    },
    "get_weather": {
        "handler": weather_tool.get_weather,
        "needs_user": False,
        "schema": _schema(
            "get_weather",
            "Get the CURRENT weather for a city or place. Always use this for any "
            "weather question — never answer from memory.",
            {"location": {"type": "string", "description": "City or place name, e.g. 'Chennai'"}},
            ["location"],
        ),
    },
    "browser_search": {
        "handler": browser_tool.browser_search,
        "needs_user": False,
        "schema": _schema(
            "browser_search",
            "Search the live web with a headless browser. Use for news, prices, "
            "facts that change over time, or anything you are not sure about. "
            "Returns result titles, snippets and source URLs.",
            {"query": {"type": "string", "description": "The search query"}},
            ["query"],
        ),
    },
    "read_webpage": {
        "handler": webpage_tool.read_webpage,
        "needs_user": False,
        "schema": _schema(
            "read_webpage",
            "Open a specific URL in a headless browser and return its title and "
            "readable text. Use when the user gives a URL or after browser_search "
            "when you need a page's full content.",
            {"url": {"type": "string", "description": "Full URL of the page to read"}},
            ["url"],
        ),
    },
    "save_memory": {
        "handler": memory_tool.save_memory,
        "needs_user": True,
        "schema": _schema(
            "save_memory",
            "Save an important fact, preference, or note about the user to "
            "long-term memory. Use when the user says 'remember ...' or shares "
            "something clearly worth keeping (name, preferences, ongoing tasks).",
            {
                "content": {"type": "string", "description": "The fact to remember, phrased as a standalone sentence"},
                "type": {
                    "type": "string",
                    "description": "One of: preference, fact, task, note",
                    "enum": ["preference", "fact", "task", "note"],
                },
                "importance": {
                    "type": "integer",
                    "description": "1 (trivial) to 10 (critical). Default 5.",
                },
            },
            ["content"],
        ),
    },
    "search_memory": {
        "handler": memory_tool.search_memory,
        "needs_user": True,
        "schema": _schema(
            "search_memory",
            "Semantically search the user's saved memories and uploaded documents. "
            "Use for questions like 'what did I say about ...' or 'what do you "
            "know about me'.",
            {"query": {"type": "string", "description": "What to look for"}},
            ["query"],
        ),
    },
    "summarize_text": {
        "handler": summarize_tool.summarize_text,
        "needs_user": False,
        "schema": _schema(
            "summarize_text",
            "Summarize a long piece of text into key points.",
            {"text": {"type": "string", "description": "The text to summarize"}},
            ["text"],
        ),
    },
    "get_current_datetime": {
        "handler": datetime_tool.get_current_datetime,
        "needs_user": False,
        "schema": _schema(
            "get_current_datetime",
            "Get the exact current local date and time. ALWAYS call this when "
            "the user asks what time or date it is — never answer a time "
            "question from memory or from earlier messages.",
            {},
            [],
        ),
    },
}


def tool_schemas() -> list[dict]:
    return [t["schema"] for t in TOOLS.values()]


async def execute_tool(name: str, arguments: dict[str, Any], user_id: str) -> tuple[str, bool]:
    """Run a tool safely. Returns (result_text, success).

    Failures are returned as text (not raised) so the LLM can explain the
    problem to the user instead of the request crashing.
    """
    entry = TOOLS.get(name)
    if entry is None:
        return f"Error: unknown tool '{name}'. Available tools: {', '.join(TOOLS)}", False

    kwargs = dict(arguments or {})
    if entry["needs_user"]:
        kwargs["user_id"] = user_id

    # Drop hallucinated arguments the handler doesn't accept
    valid_params = entry["schema"]["function"]["parameters"]["properties"].keys()
    kwargs = {k: v for k, v in kwargs.items() if k in valid_params or k == "user_id"}

    start = time.monotonic()
    try:
        result = await asyncio.wait_for(
            entry["handler"](**kwargs), timeout=settings.tool_timeout_seconds
        )
        return str(result), True
    except asyncio.TimeoutError:
        logger.error("Tool %s timed out after %ss", name, settings.tool_timeout_seconds)
        return f"Error: tool '{name}' timed out after {settings.tool_timeout_seconds}s.", False
    except Exception as exc:  # noqa: BLE001 — tool failures must not crash the agent
        logger.exception("Tool %s failed", name)
        return f"Error: tool '{name}' failed: {exc}", False
    finally:
        logger.info("Tool %s finished in %.0f ms", name, (time.monotonic() - start) * 1000)
