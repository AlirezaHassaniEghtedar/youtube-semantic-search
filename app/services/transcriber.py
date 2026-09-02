import logging
import os
import sys
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


def transcribe(model: WhisperModel, audio_path: Path) -> list[dict[str, Any]]:
    """Transcribe audio file. Returns list of {start, end, text} segments."""
    segments_out: list[dict[str, Any]] = []

    try:
        segments, _info = model.transcribe(
            str(audio_path),
            beam_size=5,
            language=None,
            task="transcribe",
            vad_filter=True,
        )
        segments = list(segments)
    except Exception as exc:
        if not _looks_like_missing_cuda_library(exc):
            raise
        logger.warning(
            "CUDA library missing/unusable at transcribe time (%s). "
            "Retrying this video on CPU instead.",
            exc,
        )
        cpu_model = _get_cpu_fallback_model()
        segments, _info = cpu_model.transcribe(
            str(audio_path),
            beam_size=5,
            language=None,
            task="transcribe",
            vad_filter=True,
        )
        segments = list(segments)

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


def _looks_like_missing_cuda_library(exc: Exception) -> bool:
    message = str(exc).lower()
    return (
        isinstance(exc, RuntimeError)
        and ".dll" in message
        and ("cublas" in message or "cudnn" in message or "cuda" in message)
    )

_cpu_fallback_model: WhisperModel | None = None
def _get_cpu_fallback_model() -> WhisperModel:
    global _cpu_fallback_model
    if _cpu_fallback_model is None:
        logger.warning(
            "Creating CPU fallback Whisper model '%s' (int8) because CUDA "
            "libraries are missing on this machine.",
            settings.WHISPER_MODEL_SIZE,
        )
        _cpu_fallback_model = WhisperModel(
            settings.WHISPER_MODEL_SIZE,
            device="cpu",
            compute_type="int8",
            cpu_threads=os.cpu_count() or 4,
            num_workers=settings.WHISPER_NUM_WORKERS,
        )
    return _cpu_fallback_model

def _register_windows_cuda_dll_dirs() -> None:
    """..."""
    if sys.platform != "win32":
        return
    try:
        import nvidia  # type: ignore
    except ImportError:
        return

    # nvidia-cublas-cu12 / nvidia-cudnn-cu12 install into a shared
    # namespace package (no __init__.py), so __file__ is None here;
    # __path__ holds the actual on-disk directories instead.
    namespace_dirs = list(getattr(nvidia, "__path__", []))
    for namespace_dir in namespace_dirs:
        for bin_dir in Path(namespace_dir).glob("*/bin"):
            try:
                os.add_dll_directory(str(bin_dir))
                logger.info("Registered CUDA DLL directory: %s", bin_dir)
            except OSError as exc:
                logger.warning("Could not register DLL directory %s: %s", bin_dir, exc)

try:
    _register_windows_cuda_dll_dirs()
except Exception:
    logger.exception(
        "Unexpected error while registering CUDA DLL directories; "
        "continuing without it (CPU fallback will still work)."
    )

_register_windows_cuda_dll_dirs()