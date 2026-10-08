"""summarize_text(text) — condense long text using the local LLM."""

from app.services import ollama_service

_SYSTEM = (
    "You are a precise summarizer. Summarize the given text into short, "
    "clear bullet points followed by a one-sentence takeaway. "
    "Keep only the important facts. Do not add information."
)

# Keep the input within a small local model's comfortable context
MAX_INPUT_CHARS = 12_000


async def summarize_text(text: str) -> str:
    if not text.strip():
        return "Nothing to summarize — the provided text is empty."
    if len(text) > MAX_INPUT_CHARS:
        text = text[:MAX_INPUT_CHARS]
    return await ollama_service.complete(f"Summarize this:\n\n{text}", system=_SYSTEM)
