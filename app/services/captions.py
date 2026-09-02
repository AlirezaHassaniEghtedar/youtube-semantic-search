import json
import logging
import re
import tempfile
from pathlib import Path
from typing import Any

import yt_dlp

from app.services.live_detection import is_upcoming_event_error
from app.services.retry import RateLimitError, looks_like_rate_limit, with_backoff
from app.services.ydl_common import base_ydl_opts, raise_if_bot_check
from app.services.youtube_pace import pace_youtube_request

logger = logging.getLogger(__name__)

PREFERRED_LANGUAGES = ["fa", "en"]

_VTT_CUE_RE = re.compile(
    r"(\d{2}:\d{2}:\d{2}\.\d{3}|\d{2}:\d{2}\.\d{3})\s*-->\s*"
    r"(\d{2}:\d{2}:\d{2}\.\d{3}|\d{2}:\d{2}\.\d{3})"
)
_VTT_TAG_RE = re.compile(r"<[^>]+>")


def _vtt_timestamp_to_seconds(ts: str) -> float:
    parts = ts.split(":")
    if len(parts) == 3:
        hours, minutes, seconds = parts
    else:
        hours = "0"
        minutes, seconds = parts
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _parse_vtt(raw: bytes) -> list[dict[str, Any]]:
    lines = raw.decode("utf-8", errors="replace").splitlines()
    segments: list[dict[str, Any]] = []
    i = 0
    while i < len(lines):
        match = _VTT_CUE_RE.search(lines[i])
        if not match:
            i += 1
            continue
        start = _vtt_timestamp_to_seconds(match.group(1))
        end = _vtt_timestamp_to_seconds(match.group(2))
        i += 1
        text_lines: list[str] = []
        while i < len(lines) and lines[i].strip():
            clean = _VTT_TAG_RE.sub("", lines[i]).strip()
            if clean:
                text_lines.append(clean)
            i += 1
        cue_text = " ".join(text_lines).strip()
        if cue_text:
            segments.append({"start": start, "end": end, "text": cue_text})
    return segments


def _parse_json3(raw: bytes) -> list[dict[str, Any]]:
    data = json.loads(raw)
    segments: list[dict[str, Any]] = []
    for event in data.get("events", []):
        segs = event.get("segs")
        if not segs:
            continue
        text = "".join(seg.get("utf8", "") for seg in segs).strip()
        if not text:
            continue
        start = float(event.get("tStartMs", 0)) / 1000.0
        duration = float(event.get("dDurationMs", 0)) / 1000.0
        segments.append({"start": start, "end": start + duration, "text": text})
    return segments


def _find_subtitle_file(directory: Path) -> Path | None:
    vtt_files = list(directory.glob("*.vtt"))
    if vtt_files:
        return vtt_files[0]
    json3_files = list(directory.glob("*.json3"))
    if json3_files:
        return json3_files[0]
    return None


def fetch_captions(youtube_video_id: str) -> list[dict[str, Any]] | None:
    """Download YouTube subtitles with yt-dlp (manual tracks, then auto-captions)."""
    url = f"https://www.youtube.com/watch?v={youtube_video_id}"
    with tempfile.TemporaryDirectory(prefix=f"yt_caption_{youtube_video_id}_") as temp_dir:
        output_dir = Path(temp_dir)
        ydl_opts: dict[str, Any] = {
            **base_ydl_opts(),
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitlesformat": "vtt",
            "subtitleslangs": list(PREFERRED_LANGUAGES),
            "outtmpl": str(output_dir / f"{youtube_video_id}.%(ext)s"),
            "ignoreerrors": False,
            "retries": 3,
            "fragment_retries": 3,
            "socket_timeout": 30,
        }
        logger.info("Trying to download captions with yt-dlp for %s", youtube_video_id)
        try:
            pace_youtube_request(f"captions_fetch:{youtube_video_id}")
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                with_backoff(lambda: ydl.download([url]))
        except Exception as exc:
            raise_if_bot_check(exc)
            if is_upcoming_event_error(exc):
                raise
            if isinstance(exc, RateLimitError) or looks_like_rate_limit(exc):
                raise RateLimitError(str(exc)) from exc
            logger.exception("yt-dlp caption download failed for %s", youtube_video_id)
            return None

        subtitle_file = _find_subtitle_file(output_dir)
        if subtitle_file is None:
            logger.info("No subtitle file was downloaded for %s", youtube_video_id)
            return None

        logger.info("Subtitle file found for %s: %s", youtube_video_id, subtitle_file.name)
        try:
            raw = subtitle_file.read_bytes()
            suffix = subtitle_file.suffix.lower()
            if suffix == ".vtt":
                segments = _parse_vtt(raw)
            elif suffix == ".json3":
                segments = _parse_json3(raw)
            else:
                logger.warning("Unsupported subtitle format for %s: %s", youtube_video_id, subtitle_file)
                return None
        except Exception:
            logger.exception("Failed to parse subtitle file for %s", youtube_video_id)
            return None

        if not segments:
            logger.info("Subtitle file was empty for %s", youtube_video_id)
            return None

        logger.info(
            "Successfully fetched %d caption segments for %s via yt-dlp",
            len(segments),
            youtube_video_id,
        )
        return segments
