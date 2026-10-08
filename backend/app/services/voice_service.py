"""Voice I/O — all local, no cloud.

STT: faster-whisper (Whisper running on CTranslate2, CPU-friendly).
     The model is loaded lazily on the first request and cached.
TTS: engine chosen by TTS_ENGINE in .env:
       auto  - Piper if installed AND a voice model is configured,
               otherwise the macOS built-in `say` command
       piper - force Piper (local neural TTS)
       say   - force macOS `say`

Both TTS paths produce WAV bytes that the frontend plays directly.
"""

import asyncio
import io
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)

_whisper_model = None  # loaded lazily; ~150MB download on first ever use
_piper_voice = None


# ---------- speech-to-text ----------

def _load_whisper():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel

        logger.info("Loading Whisper model '%s' (first load may download it)...",
                    settings.whisper_model)
        _whisper_model = WhisperModel(
            settings.whisper_model, device="cpu", compute_type="int8"
        )
        logger.info("Whisper model loaded")
    return _whisper_model


def _transcribe_sync(audio_bytes: bytes, initial_prompt: Optional[str]) -> str:
    model = _load_whisper()
    segments, info = model.transcribe(
        io.BytesIO(audio_bytes),
        language=settings.whisper_language or None,
        vad_filter=True,  # trims leading/trailing silence
        # Biases the decoder toward the user's own vocabulary (project
        # names, people) — otherwise unusual proper nouns get "corrected"
        # into similar-sounding English words.
        initial_prompt=initial_prompt,
    )
    text = " ".join(segment.text.strip() for segment in segments).strip()
    logger.info("Transcribed %d bytes of audio (lang=%s): %r",
                len(audio_bytes), info.language, text[:120])
    return text


async def transcribe(audio_bytes: bytes, initial_prompt: Optional[str] = None) -> str:
    """Audio (webm/mp4/wav/ogg — anything ffmpeg decodes) → text."""
    return await asyncio.to_thread(_transcribe_sync, audio_bytes, initial_prompt)


# ---------- text-to-speech ----------

def _piper_available() -> bool:
    if not settings.piper_voice_path:
        return False
    if not Path(settings.piper_voice_path).exists():
        logger.warning("PIPER_VOICE_PATH set but file not found: %s",
                       settings.piper_voice_path)
        return False
    try:
        import piper  # noqa: F401
        return True
    except ImportError:
        return False


def _synthesize_piper_sync(text: str) -> bytes:
    global _piper_voice
    import wave

    from piper import PiperVoice

    if _piper_voice is None:
        logger.info("Loading Piper voice %s ...", settings.piper_voice_path)
        _piper_voice = PiperVoice.load(settings.piper_voice_path)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav_file:
        _piper_voice.synthesize_wav(text, wav_file)
    return buf.getvalue()


def _synthesize_say_sync(text: str) -> bytes:
    """macOS built-in TTS. Writes a WAV to a temp file and returns its bytes."""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        cmd = ["say", "-o", tmp_path, "--data-format=LEI16@22050"]
        if settings.say_voice:
            cmd += ["-v", settings.say_voice]
        cmd.append(text)
        subprocess.run(cmd, check=True, capture_output=True, timeout=60)
        return Path(tmp_path).read_bytes()
    finally:
        Path(tmp_path).unlink(missing_ok=True)


async def synthesize(text: str) -> tuple[bytes, str]:
    """Text → (wav_bytes, engine_name). Falls back Piper → say on failure."""
    engine = settings.tts_engine.lower()
    use_piper = engine == "piper" or (engine == "auto" and _piper_available())

    if use_piper:
        try:
            return await asyncio.to_thread(_synthesize_piper_sync, text), "piper"
        except Exception as exc:  # noqa: BLE001 — fall back to `say`
            logger.exception("Piper synthesis failed, falling back to 'say': %s", exc)

    return await asyncio.to_thread(_synthesize_say_sync, text), "say"


def stt_ready() -> tuple[bool, str]:
    """(available, detail) — used by /health without loading the model."""
    try:
        import faster_whisper  # noqa: F401
        return True, f"faster-whisper ({settings.whisper_model})"
    except ImportError:
        return False, "faster-whisper not installed"
