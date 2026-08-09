import logging
from pathlib import Path
from typing import Any

from faster_whisper import WhisperModel

from app.config import settings
from app.services.downloader import cleanup_audio_file

logger = logging.getLogger(__name__)


def load_whisper_model() -> WhisperModel:
    return WhisperModel(
        settings.WHISPER_MODEL_SIZE,
        device="cpu",
        compute_type="int8",
    )


def transcribe(model: WhisperModel, audio_path: Path) -> list[dict[str, Any]]:
    """Transcribe audio file. Returns list of {start, end, text} segments."""
    segments_out: list[dict[str, Any]] = []

    segments, _info = model.transcribe(
        str(audio_path),
        beam_size=5,
        language=None,
        task="transcribe",
        vad_filter=True,
    )

    for segment in segments:
        text = segment.text.strip()
        if text:
            segments_out.append(
                {
                    "start": float(segment.start),
                    "end": float(segment.end),
                    "text": text,
                }
            )

    cleanup_audio_file(audio_path)
    return segments_out
