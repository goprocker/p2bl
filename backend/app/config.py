"""Application configuration.

All settings come from environment variables (or a .env file in backend/).
Nothing is hardcoded — copy .env.example to .env and adjust.
"""

import logging
import sys
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # MongoDB
    mongo_uri: str = "mongodb://localhost:27017"
    mongo_db: str = "local_ai_agent"

    # Ollama
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "qwen3"
    ollama_embed_model: str = "nomic-embed-text"
    ollama_temperature: float = 0.3

    # Groq chat provider (preferred when a key is configured)
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"

    @property
    def llm_provider(self) -> str:
        return "groq" if self.groq_api_key else "ollama"

    @property
    def chat_model(self) -> str:
        return self.groq_model if self.groq_api_key else self.ollama_model

    # Vector store
    chroma_dir: str = "./chroma_data"

    # Weather
    openweather_api_key: str = ""

    # Agent behaviour
    max_agent_steps: int = 6
    tool_timeout_seconds: int = 60
    history_limit: int = 12

    # Smart room (ProjectMyRoom API bridge)
    myroom_api_url: str = "http://localhost:3100"
    myroom_api_token: str = ""  # empty = smart-room tools report 'not configured'
    automation_live_enabled: bool = False

    # Voice
    whisper_model: str = "base"          # tiny | base | small | medium (bigger = slower, better)
    whisper_language: str = "en"         # empty string = auto-detect
    tts_engine: str = "auto"             # auto | piper | say
    piper_voice_path: str = ""           # path to a .onnx Piper voice (auto uses it if set)
    say_voice: str = ""                  # macOS `say` voice name, e.g. "Samantha"; empty = system default

    # API
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    # Logging
    log_level: str = "INFO"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()


def setup_logging() -> None:
    """Configure root logging once, at startup."""
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        stream=sys.stdout,
        force=True,
    )
    # Quieten noisy third-party loggers
    for noisy in ("httpx", "httpcore", "chromadb", "pymongo", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
