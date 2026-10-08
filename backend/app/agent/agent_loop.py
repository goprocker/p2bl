"""The agent loop — the core of the assistant.

One user message flows through:

  1. Load recent conversation history (MongoDB).
  2. Automatically recall relevant memories (Chroma vector search).
  3. Ask the local LLM (Ollama), offering the tool schemas.
  4. If the LLM requests tool calls: run them, log them, feed results back,
     and go to step 3 again (up to MAX_AGENT_STEPS iterations).
  5. When the LLM answers with plain text, persist the exchange and return.

The loop is implemented once, as an async generator of events
(run_agent_stream) so the /chat/stream endpoint can push live progress to
the UI. run_agent() wraps it for callers that just want the final result
(/chat, /voice/chat).

Event types yielded:
  {"type": "status", "message": str}                  a new reasoning round began
  {"type": "token", "text": str}                      a piece of the answer text
  {"type": "reset"}                                   discard streamed text so far
  {"type": "tool_start", "tool", "arguments"}         a tool is about to run
  {"type": "tool_end", ...ToolCallInfo fields}        a tool finished
  {"type": "done", "answer", "conversation_id",
   "tool_calls", "memories_used"}                     always the final event
"""

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from app.agent.prompts import build_system_prompt, freshness_note
from app.agent.tool_registry import execute_tool, tool_schemas
from app.config import settings
from app.db import mongo
from app.services import ollama_service
from app.tools import memory_tool

logger = logging.getLogger(__name__)

RESULT_PREVIEW_CHARS = 400

# "turn off the fan", "switch on all devices", "Ceiling Fan: turn off this"...
_DEVICE_CMD_RE = re.compile(
    r"\b(turn|switch|toggle|power|shut)\b[^.?!]*\b(on|off)\b"
    r"|\b(on|off)\b[^.?!]*\b(light|fan|blind|ac|device|devices)\b",
    re.IGNORECASE,
)

_DEVICE_RETRY_NOTE = (
    "[System check: your last reply claimed a device changed state, but you "
    "did NOT call control_device, so NOTHING actually changed — the claim "
    "was fiction. Call control_device NOW to really do it (use device='all' "
    "for every device), then answer from the tool result only.]"
)


def _fallback_device_args(message: str) -> dict:
    """Best-effort control_device arguments when the model refuses to call it.

    'all' covers every-device requests; otherwise the raw message works
    because control_device's fuzzy matcher picks device names/types out of it.
    """
    state = "off" if re.search(r"\boff\b", message, re.IGNORECASE) else "on"
    if re.search(r"\b(all|every|everything)\b", message, re.IGNORECASE):
        device = "all"
    else:
        device = message
    return {"device": device, "state": state}


@dataclass
class AgentResult:
    answer: str
    conversation_id: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    memories_used: list[str] = field(default_factory=list)


async def run_agent_stream(
    message: str,
    user_id: str,
    conversation_id: str | None,
    model: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    await mongo.ensure_user(user_id)

    # Reuse the conversation if it exists, otherwise start a new one
    if not conversation_id or not await mongo.conversation_exists(conversation_id):
        conversation_id = await mongo.create_conversation(user_id)

    tool_calls_made: list[dict[str, Any]] = []
    answer = ""
    content_parts: list[str] = []  # also read by the cancellation handler below

    try:
        history = await mongo.get_recent_messages(conversation_id, settings.history_limit)
        memories_used = await memory_tool.recall(user_id, message)

        # The freshness note rides inside the newest user turn (not a system
        # message): small models parrot stale times from their own earlier
        # answers unless the correction sits in the very last turn they read.
        # Only the LLM payload is annotated — Mongo stores the clean `message`.
        messages: list[dict] = [
            {"role": "system", "content": build_system_prompt(memories_used)},
            *history,
            {"role": "user", "content": f"{message}\n\n{freshness_note()}"},
        ]

        forced_device_retry = False
        for step in range(settings.max_agent_steps):
            # On the final allowed step, withhold tools to force a text answer
            offer_tools = tool_schemas() if step < settings.max_agent_steps - 1 else None
            yield {"type": "status", "message": "thinking"}

            content_parts = []
            tool_calls: list[Any] = []
            # Some models (e.g. gemma) can't do tool calling — Ollama rejects the
            # request up front. Retry that round without tools: the assistant
            # degrades to plain chat for this model instead of erroring out.
            for offered in (offer_tools, None):
                try:
                    async for chunk in ollama_service.chat_stream(
                        messages, tools=offered, model=model
                    ):
                        msg = _get(chunk, "message")
                        piece = _get(msg, "content") or ""
                        if piece:
                            content_parts.append(piece)
                            yield {"type": "token", "text": piece}
                        calls = _get(msg, "tool_calls") or []
                        if calls:
                            tool_calls.extend(calls)
                    break
                except Exception as exc:  # noqa: BLE001 — retry only the no-tools case
                    if offered and "does not support tools" in str(exc).lower():
                        logger.warning(
                            "Model %s has no tool support — answering without tools",
                            model or settings.chat_model,
                        )
                        continue
                    raise

            full_content = "".join(content_parts)

            if not tool_calls:
                # Small models imitate earlier no-tool answers in the history
                # and claim a device changed without calling control_device.
                # The claim is fiction — discard it and force ONE retry that
                # demands the tool; if the model refuses again, run the tool
                # from here so the device REALLY changes.
                needs_device_tool = _DEVICE_CMD_RE.search(message) and not any(
                    t["tool"] == "control_device" for t in tool_calls_made
                )
                if needs_device_tool and offer_tools and not forced_device_retry:
                    forced_device_retry = True
                    logger.warning(
                        "Device command answered without control_device — forcing a tool round"
                    )
                    yield {"type": "reset"}
                    messages.append({"role": "assistant", "content": full_content})
                    messages.append({"role": "user", "content": _DEVICE_RETRY_NOTE})
                    continue
                if needs_device_tool and offer_tools:
                    logger.warning(
                        "Model refused control_device twice — executing it directly"
                    )
                    yield {"type": "reset"}
                    arguments = _fallback_device_args(message)
                    yield {"type": "tool_start", "tool": "control_device", "arguments": arguments}
                    start = time.monotonic()
                    result, success = await execute_tool("control_device", arguments, user_id)
                    duration_ms = int((time.monotonic() - start) * 1000)
                    record = {
                        "tool": "control_device",
                        "arguments": arguments,
                        "result_preview": result[:RESULT_PREVIEW_CHARS],
                        "success": success,
                        "duration_ms": duration_ms,
                    }
                    tool_calls_made.append(record)
                    await mongo.log_tool_call(
                        user_id, conversation_id, "control_device", arguments,
                        result[:RESULT_PREVIEW_CHARS], success, duration_ms,
                    )
                    yield {"type": "tool_end", **record}
                    answer = result
                    yield {"type": "token", "text": answer}
                    break
                answer = ollama_service.clean_content(full_content)
                break

            # Tool round: any stray content streamed so far was not the answer
            if full_content.strip():
                yield {"type": "reset"}

            messages.append(
                {
                    "role": "assistant",
                    "content": full_content,
                    "tool_calls": [_tc_to_dict(tc) for tc in tool_calls],
                }
            )
            for call in tool_calls:
                name, arguments = _parse_call(call)
                logger.info("Agent step %d: calling %s(%s)", step + 1, name, arguments)
                yield {"type": "tool_start", "tool": name, "arguments": arguments}

                start = time.monotonic()
                result, success = await execute_tool(name, arguments, user_id)
                duration_ms = int((time.monotonic() - start) * 1000)

                record = {
                    "tool": name,
                    "arguments": arguments,
                    "result_preview": result[:RESULT_PREVIEW_CHARS],
                    "success": success,
                    "duration_ms": duration_ms,
                }
                tool_calls_made.append(record)
                await mongo.log_tool_call(
                    user_id, conversation_id, name, arguments,
                    result[:RESULT_PREVIEW_CHARS], success, duration_ms,
                )
                yield {"type": "tool_end", **record}
                messages.append({
                    "role": "tool",
                    "content": result,
                    "tool_name": name,
                    "tool_call_id": _get(call, "id"),
                })

        if not answer:
            answer = (
                "I ran out of reasoning steps before finishing. "
                "Please try rephrasing or splitting your request."
            )
            yield {"type": "token", "text": answer}

        await mongo.add_message(conversation_id, user_id, "user", message)
        await mongo.add_message(conversation_id, user_id, "assistant", answer)

        yield {
            "type": "done",
            "answer": answer,
            "conversation_id": conversation_id,
            "tool_calls": tool_calls_made,
            "memories_used": memories_used,
        }
    except (asyncio.CancelledError, GeneratorExit):
        # The client hit Stop (or disconnected): the SSE connection dropped and
        # the server cancelled us mid-stream. Cancellation also tears down the
        # Ollama request, so the model stops generating. Persist the exchange
        # with whatever partial text streamed, so history stays coherent.
        partial = ollama_service.clean_content("".join(content_parts))
        stopped_answer = f"{partial} … [stopped by user]" if partial else "[stopped by user]"

        async def _persist() -> None:
            await mongo.add_message(conversation_id, user_id, "user", message)
            await mongo.add_message(conversation_id, user_id, "assistant", stopped_answer)

        try:
            # shield: the request task can be cancelled again while we're
            # writing — the shielded task finishes both writes regardless.
            await asyncio.shield(_persist())
        except asyncio.CancelledError:
            pass  # writes continue in the shielded task
        except Exception:  # noqa: BLE001 — best-effort during teardown
            logger.exception("Could not persist the stopped exchange")
        logger.info("Generation stopped by client (conversation %s)", conversation_id)
        raise


async def run_agent(
    message: str,
    user_id: str,
    conversation_id: str | None,
    model: str | None = None,
) -> AgentResult:
    """Run the loop to completion and return only the final result."""
    async for event in run_agent_stream(message, user_id, conversation_id, model):
        if event["type"] == "done":
            return AgentResult(
                answer=event["answer"],
                conversation_id=event["conversation_id"],
                tool_calls=event["tool_calls"],
                memories_used=event["memories_used"],
            )
    raise RuntimeError("Agent stream ended without a 'done' event")


# ---------- helpers that tolerate both dict and pydantic responses ----------
# The ollama library returns pydantic models in recent versions and dicts in
# older ones; these helpers keep the loop working with either.

def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _parse_call(call: Any) -> tuple[str, dict]:
    function = _get(call, "function")
    name = _get(function, "name") or ""
    arguments = _get(function, "arguments") or {}
    if isinstance(arguments, str):  # some models return JSON-encoded args
        import json

        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            arguments = {}
    return name, dict(arguments)


def _tc_to_dict(call: Any) -> dict:
    if isinstance(call, dict):
        return call
    if hasattr(call, "model_dump"):
        return call.model_dump(exclude_none=True)
    name, arguments = _parse_call(call)
    return {"function": {"name": name, "arguments": arguments}}
