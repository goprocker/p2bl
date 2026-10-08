"""MongoDB layer (async, via Motor).

Collections:
  users         - user profiles / preferences
  conversations - one doc per chat session
  messages      - every user/assistant message
  memories      - long-term memories (source of truth; vectors live in Chroma)
  tool_calls    - audit log of every tool invocation
  documents     - uploaded files (chunk text lives here, vectors in Chroma)
"""

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from bson import ObjectId
from bson.errors import InvalidId
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.config import settings

logger = logging.getLogger(__name__)

_client: Optional[AsyncIOMotorClient] = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_db() -> AsyncIOMotorDatabase:
    global _client
    if _client is None:
        _client = AsyncIOMotorClient(settings.mongo_uri, serverSelectionTimeoutMS=3000)
    return _client[settings.mongo_db]


async def ping() -> bool:
    try:
        await get_db().command("ping")
        return True
    except Exception as exc:  # noqa: BLE001 — health check must never raise
        logger.warning("MongoDB not reachable: %s", exc)
        return False


async def close() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None


# ---------- users ----------

async def ensure_user(user_id: str) -> None:
    """Create the user profile on first contact (idempotent)."""
    await get_db().users.update_one(
        {"_id": user_id},
        {
            "$setOnInsert": {"created_at": _now(), "preferences": {}},
            "$set": {"last_seen_at": _now()},
        },
        upsert=True,
    )


# ---------- conversations & messages ----------

async def create_conversation(user_id: str) -> str:
    result = await get_db().conversations.insert_one(
        {"user_id": user_id, "created_at": _now(), "title": None}
    )
    return str(result.inserted_id)


async def conversation_exists(conversation_id: str) -> bool:
    oid = _to_object_id(conversation_id)
    if oid is None:
        return False
    return await get_db().conversations.find_one({"_id": oid}) is not None


async def add_message(conversation_id: str, user_id: str, role: str, content: str) -> str:
    result = await get_db().messages.insert_one(
        {
            "conversation_id": conversation_id,
            "user_id": user_id,
            "role": role,
            "content": content,
            "created_at": _now(),
        }
    )
    oid = _to_object_id(conversation_id)
    if oid is not None:
        await get_db().conversations.update_one(
            {"_id": oid},
            {"$set": {"last_message_at": _now()}, "$inc": {"message_count": 1}},
        )
        if role == "user":
            # First user message becomes the conversation title
            await get_db().conversations.update_one(
                {"_id": oid, "title": None}, {"$set": {"title": content[:80]}}
            )
    return str(result.inserted_id)


async def list_conversations(user_id: str, limit: int = 50) -> list[dict]:
    """Conversations for the history panel, most recently active first."""
    pipeline = [
        # Skip conversations that never got a message (failed/abandoned requests)
        {"$match": {"user_id": user_id, "message_count": {"$gt": 0}}},
        {"$addFields": {"sort_ts": {"$ifNull": ["$last_message_at", "$created_at"]}}},
        {"$sort": {"sort_ts": -1}},
        {"$limit": limit},
    ]
    docs = await get_db().conversations.aggregate(pipeline).to_list(length=limit)
    out = []
    for d in docs:
        out.append(
            {
                "id": str(d["_id"]),
                "title": d.get("title") or "Conversation",
                "created_at": d["created_at"].isoformat(),
                "last_message_at": (d.get("last_message_at") or d["created_at"]).isoformat(),
                "message_count": d.get("message_count", 0),
            }
        )
    return out


async def get_all_messages(conversation_id: str) -> list[dict]:
    """Every message of one conversation, oldest first (for reopening it)."""
    cursor = (
        get_db()
        .messages.find({"conversation_id": conversation_id})
        .sort("created_at", 1)
    )
    docs = await cursor.to_list(length=1000)
    return [
        {"role": d["role"], "content": d["content"], "created_at": d["created_at"].isoformat()}
        for d in docs
    ]


async def get_recent_messages(conversation_id: str, limit: int) -> list[dict]:
    """Last `limit` messages, oldest first, as LLM-ready dicts."""
    cursor = (
        get_db()
        .messages.find({"conversation_id": conversation_id})
        .sort("created_at", -1)
        .limit(limit)
    )
    docs = await cursor.to_list(length=limit)
    return [{"role": d["role"], "content": d["content"]} for d in reversed(docs)]


# ---------- memories ----------

async def insert_memory(
    user_id: str, content: str, mem_type: str, importance: int, source: str
) -> str:
    now = _now()
    result = await get_db().memories.insert_one(
        {
            "user_id": user_id,
            "type": mem_type,
            "content": content,
            "importance": importance,
            "source": source,
            "created_at": now,
            "updated_at": now,
        }
    )
    return str(result.inserted_id)


async def list_memories(user_id: str, limit: int = 100) -> list[dict]:
    cursor = (
        get_db()
        .memories.find({"user_id": user_id})
        .sort([("importance", -1), ("created_at", -1)])
        .limit(limit)
    )
    return [_serialize(d) for d in await cursor.to_list(length=limit)]


async def get_memories_by_ids(ids: list[str]) -> list[dict]:
    oids = [oid for oid in (_to_object_id(i) for i in ids) if oid is not None]
    if not oids:
        return []
    docs = await get_db().memories.find({"_id": {"$in": oids}}).to_list(length=len(oids))
    # Preserve the ranking order the vector search returned
    by_id = {str(d["_id"]): d for d in docs}
    return [_serialize(by_id[i]) for i in ids if i in by_id]


async def delete_memory(memory_id: str) -> bool:
    oid = _to_object_id(memory_id)
    if oid is None:
        return False
    result = await get_db().memories.delete_one({"_id": oid})
    return result.deleted_count == 1


# ---------- tool call audit log ----------

async def log_tool_call(
    user_id: str,
    conversation_id: str,
    tool: str,
    arguments: dict[str, Any],
    result_preview: str,
    success: bool,
    duration_ms: int,
) -> None:
    try:
        await get_db().tool_calls.insert_one(
            {
                "user_id": user_id,
                "conversation_id": conversation_id,
                "tool": tool,
                "arguments": arguments,
                "result_preview": result_preview,
                "success": success,
                "duration_ms": duration_ms,
                "created_at": _now(),
            }
        )
    except Exception:  # noqa: BLE001 — logging must never break the agent
        logger.exception("Failed to persist tool call log")


# ---------- automation audit ----------

async def get_automation_rule_states() -> dict[str, bool]:
    docs = await get_db().automation_rule_states.find({}).to_list(length=100)
    return {d["_id"]: d["enabled"] for d in docs}


async def set_automation_rule_state(rule_id: str, enabled: bool) -> None:
    await get_db().automation_rule_states.update_one(
        {"_id": rule_id}, {"$set": {"enabled": enabled, "updated_at": _now()}}, upsert=True
    )


async def save_automation_decision(decision: dict[str, Any]) -> None:
    await get_db().automation_decisions.insert_one({**decision, "_id": decision["id"]})


async def list_automation_decisions(limit: int = 20) -> list[dict]:
    docs = await (
        get_db().automation_decisions.find({}).sort("created_at", -1).limit(limit)
    ).to_list(length=limit)
    out = []
    for doc in docs:
        doc["id"] = str(doc.pop("_id"))
        out.append(doc)
    return out


# ---------- documents ----------

async def insert_document(user_id: str, filename: str, chunks: list[str]) -> str:
    result = await get_db().documents.insert_one(
        {
            "user_id": user_id,
            "filename": filename,
            "chunks": chunks,
            "chunk_count": len(chunks),
            "created_at": _now(),
        }
    )
    return str(result.inserted_id)


# ---------- helpers ----------

def _to_object_id(value: str) -> Optional[ObjectId]:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        return None


def _serialize(doc: dict) -> dict:
    """Convert a Mongo doc to a JSON-safe dict."""
    out = {**doc, "id": str(doc["_id"])}
    out.pop("_id", None)
    for key in ("created_at", "updated_at"):
        if isinstance(out.get(key), datetime):
            out[key] = out[key].isoformat()
    return out
