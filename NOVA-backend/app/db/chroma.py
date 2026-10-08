"""ChromaDB vector store (embedded, on-disk — no separate server needed).

Embeddings are generated locally through Ollama (nomic-embed-text) and passed
to Chroma explicitly, so Chroma is used purely as a vector index. MongoDB
remains the source of truth for memory/document content.

Chroma's client is synchronous; every public function here wraps the work in
asyncio.to_thread so the FastAPI event loop never blocks.
"""

import asyncio
import logging
from typing import Optional

import chromadb

from app.config import settings
from app.services import ollama_service

logger = logging.getLogger(__name__)

MEMORIES = "memories"
DOCUMENTS = "documents"

_client: Optional[chromadb.ClientAPI] = None


def _get_client() -> chromadb.ClientAPI:
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(path=settings.chroma_dir)
    return _client


def _collection(name: str):
    return _get_client().get_or_create_collection(name, metadata={"hnsw:space": "cosine"})


# ---------- write ----------

async def add(
    collection: str, doc_id: str, content: str, metadata: dict
) -> None:
    """Embed `content` via Ollama and upsert it into the given collection."""
    [embedding] = await ollama_service.embed([content])
    await asyncio.to_thread(
        lambda: _collection(collection).upsert(
            ids=[doc_id], embeddings=[embedding], documents=[content], metadatas=[metadata]
        )
    )


async def add_batch(
    collection: str, ids: list[str], contents: list[str], metadatas: list[dict]
) -> None:
    embeddings = await ollama_service.embed(contents)
    await asyncio.to_thread(
        lambda: _collection(collection).upsert(
            ids=ids, embeddings=embeddings, documents=contents, metadatas=metadatas
        )
    )


async def delete(collection: str, doc_id: str) -> None:
    await asyncio.to_thread(lambda: _collection(collection).delete(ids=[doc_id]))


# ---------- read ----------

async def dump(collection: str, limit: int = 100) -> dict:
    """Read-only inspection of a collection (for the /vectors endpoint)."""

    def _run():
        col = _collection(collection)
        result = col.get(include=["documents", "metadatas", "embeddings"], limit=limit)
        items = []
        for i, doc_id in enumerate(result["ids"]):
            emb = result["embeddings"][i]
            items.append(
                {
                    "id": doc_id,
                    "content": result["documents"][i],
                    "metadata": result["metadatas"][i],
                    "vector_dims": len(emb),
                    "vector_preview": [round(float(x), 4) for x in emb[:8]],
                }
            )
        return {"total_in_collection": col.count(), "items": items}

    return await asyncio.to_thread(_run)

async def query(
    collection: str, text: str, user_id: str, k: int = 5, max_distance: float = 0.75
) -> list[dict]:
    """Semantic search. Returns [{id, content, metadata, distance}], best first.

    Results with cosine distance above `max_distance` are dropped — they are
    too unrelated to be worth injecting into the prompt.
    """
    [embedding] = await ollama_service.embed([text])

    def _run():
        col = _collection(collection)
        if col.count() == 0:
            return None
        return col.query(
            query_embeddings=[embedding],
            n_results=k,
            where={"user_id": user_id},
            include=["documents", "metadatas", "distances"],
        )

    result = await asyncio.to_thread(_run)
    if not result or not result["ids"] or not result["ids"][0]:
        return []

    hits = []
    for i, doc_id in enumerate(result["ids"][0]):
        distance = result["distances"][0][i]
        if distance > max_distance:
            continue
        hits.append(
            {
                "id": doc_id,
                "content": result["documents"][0][i],
                "metadata": result["metadatas"][0][i],
                "distance": distance,
            }
        )
    return hits
