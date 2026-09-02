import logging
import os
from pathlib import Path
from typing import Any

from faster_whisper import WhisperModel

from app.config import settings
from app.services.downloader import cleanup_audio_file

logger = logging.getLogger(__name__)


def load_whisper_model() -> WhisperModel:
    if settings.WHISPER_DEVICE.lower() == "cuda":
        try:
            model = WhisperModel(
                settings.WHISPER_MODEL_SIZE,
                device="cuda",
                compute_type=settings.WHISPER_COMPUTE_TYPE,
                num_workers=settings.WHISPER_NUM_WORKERS,
            )
            logger.info(
                "Loaded Whisper model '%s' on CUDA (compute_type=%s, num_workers=%d)",
                settings.WHISPER_MODEL_SIZE,
                settings.WHISPER_COMPUTE_TYPE,
                settings.WHISPER_NUM_WORKERS,
            )
            return model
        except Exception:
            logger.exception(
                "Failed to load Whisper model on CUDA, falling back to CPU. "
                "Check that your NVIDIA driver and the CUDA/cuDNN libraries "
                "(installed via requirements.txt) are present."
            )

    model = WhisperModel(
        settings.WHISPER_MODEL_SIZE,
        device="cpu",
        compute_type="int8",
        cpu_threads=os.cpu_count() or 4,
        num_workers=settings.WHISPER_NUM_WORKERS,
    )
    logger.info("Loaded Whisper model '%s' on CPU (int8)", settings.WHISPER_MODEL_SIZE)
    return model


def transcribe(model: WhisperModel, audio_path: Path, cleanup: bool = True) -> list[dict[str, Any]]:
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

    if cleanup:
        cleanup_audio_file(audio_path)
    return segments_out
