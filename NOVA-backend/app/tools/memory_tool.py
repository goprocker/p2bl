"""Memory tools — MongoDB is the source of truth, Chroma powers semantic search.

save_memory   : store a fact/preference/note (Mongo) + its embedding (Chroma)
search_memory : semantic search over saved memories AND uploaded documents
recall        : silent memory lookup done automatically before every chat turn
"""

import logging

from app.db import chroma, mongo
from app.services import ollama_service

logger = logging.getLogger(__name__)


# Candidates within this distance MIGHT be the same fact being updated.
# Distance alone can't decide (short sentences sharing a name land ~0.3,
# true contradictions ~0.26), so each candidate is confirmed by the LLM.
SUPERSEDE_CANDIDATE_DISTANCE = 0.45


async def _is_same_fact(new: str, old: str) -> bool:
    """LLM yes/no: does `new` update/replace the same fact as `old`?"""
    prompt = (
        f'OLD fact about the user: "{old}"\n'
        f'NEW fact about the user: "{new}"\n\n'
        "Does the NEW fact update, correct, or restate the SAME piece of "
        "information as the OLD fact (so the OLD one is now outdated)? "
        "If they describe different things — even about the same person — "
        "answer no. Answer with exactly one word: yes or no."
    )
    try:
        reply = await ollama_service.complete(prompt, system="Answer only 'yes' or 'no'.")
        return reply.strip().lower().startswith("y")
    except Exception:  # noqa: BLE001 — if unsure, never delete
        logger.exception("Same-fact check failed; keeping the old memory")
        return False


async def save_memory(
    content: str,
    type: str = "fact",  # noqa: A002 — matches the tool schema parameter name
    importance: int = 5,
    user_id: str = "default_user",
    source: str = "chat",
) -> str:
    importance = max(1, min(10, int(importance)))

    # Supersede: if a very similar memory exists, this save REPLACES it —
    # otherwise corrections pile up as contradictions ("loves coffee" AND
    # "hates coffee") and the model picks whichever it likes.
    replaced = []
    try:
        similar = await chroma.query(
            chroma.MEMORIES, content, user_id, k=3,
            max_distance=SUPERSEDE_CANDIDATE_DISTANCE,
        )
        for hit in similar:
            if not await _is_same_fact(content, hit["content"]):
                continue
            await mongo.delete_memory(hit["id"])
            await chroma.delete(chroma.MEMORIES, hit["id"])
            replaced.append(hit["content"])
            logger.info("Memory superseded: %r -> %r", hit["content"][:60], content[:60])
    except Exception:  # noqa: BLE001 — saving must not fail because of cleanup
        logger.exception("Supersede check failed; saving without replacement")

    memory_id = await mongo.insert_memory(user_id, content, type, importance, source)
    await chroma.add(
        chroma.MEMORIES,
        memory_id,
        content,
        {"user_id": user_id, "type": type, "importance": importance},
    )
    logger.info("Saved memory %s (%s, importance=%d)", memory_id, type, importance)

    if replaced:
        old = "; ".join(f"'{r}'" for r in replaced)
        return f"Memory saved: {content} (this replaced the outdated memory {old})"
    return f"Memory saved (id: {memory_id}): {content}"


async def search_memory(query: str, user_id: str = "default_user") -> str:
    """Semantic search across memories and uploaded documents."""
    memory_hits = await chroma.query(chroma.MEMORIES, query, user_id, k=5)
    doc_hits = await chroma.query(chroma.DOCUMENTS, query, user_id, k=3)

    if not memory_hits and not doc_hits:
        return f"No stored memories or documents matched: '{query}'"

    lines = []
    if memory_hits:
        # Pull full records from Mongo so results include type/importance/date
        records = await mongo.get_memories_by_ids([h["id"] for h in memory_hits])
        found_ids = {r["id"] for r in records}
        lines.append("Relevant memories:")
        for r in records:
            lines.append(f"- [{r['type']}] {r['content']} (saved {r['created_at'][:10]})")
        # Vector hits whose Mongo doc was deleted are silently skipped
        for h in memory_hits:
            if h["id"] not in found_ids:
                await chroma.delete(chroma.MEMORIES, h["id"])  # clean up orphan
    if doc_hits:
        lines.append("\nRelevant document excerpts:")
        for h in doc_hits:
            filename = h["metadata"].get("filename", "unknown file")
            lines.append(f"- From '{filename}': {h['content'][:400]}")

    return "\n".join(lines)


async def recall(user_id: str, query: str, k: int = 4) -> list[str]:
    """Best-effort automatic recall used by the agent loop (never raises)."""
    try:
        hits = await chroma.query(chroma.MEMORIES, query, user_id, k=k, max_distance=0.6)
        return [h["content"] for h in hits]
    except Exception as exc:  # noqa: BLE001 — recall is optional context
        logger.warning("Automatic memory recall failed: %s", exc)
        return []


async def delete_memory(memory_id: str) -> bool:
    """Delete from both stores (used by the DELETE /memory/{id} endpoint)."""
    deleted = await mongo.delete_memory(memory_id)
    if deleted:
        try:
            await chroma.delete(chroma.MEMORIES, memory_id)
        except Exception:  # noqa: BLE001 — Mongo delete already succeeded
            logger.exception("Failed to delete vector for memory %s", memory_id)
    return deleted
