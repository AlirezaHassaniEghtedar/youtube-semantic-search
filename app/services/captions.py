import json
import logging
import re
from typing import Any
import httpx
import yt_dlp
import tempfile
from pathlib import Path


from app.services.retry import RateLimitError, looks_like_rate_limit, with_backoff
from app.services.ydl_common import base_ydl_opts, raise_if_bot_check

_HTTP_TIMEOUT = 30.0

logger = logging.getLogger(__name__)

PREFERRED_LANGUAGES = ["en", "fa"]

_VTT_CUE_RE = re.compile(
    r"(\d{2}:\d{2}:\d{2}\.\d{3}|\d{2}:\d{2}\.\d{3})\s*-->\s*"
    r"(\d{2}:\d{2}:\d{2}\.\d{3}|\d{2}:\d{2}\.\d{3})"
)

_VTT_TAG_RE = re.compile(r"<[^>]+>")


def _match_language(available: dict[str, Any], language: str) -> str | None:
    """Find the best key in an available-tracks dict for a language code."""
    if language in available:
        return language
    prefix = f"{language}-"
    for key in available:
        if key.startswith(prefix):
            return key
    return None


def _pick_track_url(tracks: list[dict[str, Any]]) -> tuple[str, str] | None:
    """Pick a subtitle format (preferring json3, then vtt) from yt-dlp's list."""
    by_ext = {track.get("ext"): track.get("url") for track in tracks}
    for ext in ("json3", "vtt"):
        url = by_ext.get(ext)
        if url:
            return ext, url
    return None


def _vtt_timestamp_to_seconds(ts: str) -> float:
    parts = ts.split(":")

    if len(parts) == 3:
        hours, minutes, seconds = parts
    else:
        hours = "0"
        minutes, seconds = parts

    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _parse_vtt(raw: bytes) -> list[dict[str, Any]]:
    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()

    segments: list[dict[str, Any]] = []

    i = 0

    while i < len(lines):
        match = _VTT_CUE_RE.search(lines[i])

        if match:
            start = _vtt_timestamp_to_seconds(match.group(1))
            end = _vtt_timestamp_to_seconds(match.group(2))

            i += 1

            text_lines = []

            while i < len(lines) and lines[i].strip():
                clean = _VTT_TAG_RE.sub("", lines[i]).strip()

                if clean:
                    text_lines.append(clean)

                i += 1

            cue_text = " ".join(text_lines).strip()

            if cue_text:
                segments.append(
                    {
                        "start": start,
                        "end": end,
                        "text": cue_text,
                    }
                )
        else:
            i += 1

    return segments


def _parse_json3(raw: bytes) -> list[dict[str, Any]]:
    data = json.loads(raw)

    segments: list[dict[str, Any]] = []

    for event in data.get("events", []):
        segs = event.get("segs")

        if not segs:
            continue

        text = "".join(
            seg.get("utf8", "")
            for seg in segs
        ).strip()

        if not text:
            continue

        start = float(
            event.get("tStartMs", 0)
        ) / 1000.0

        duration = float(
            event.get("dDurationMs", 0)
        ) / 1000.0

        segments.append(
            {
                "start": start,
                "end": start + duration,
                "text": text,
            }
        )

    return segments


def _find_subtitle_file(directory: Path) -> Path | None:
    """
    Find the subtitle file created by yt-dlp.

    Prefer VTT because it is stable and easy to parse.
    """

    vtt_files = list(directory.glob("*.vtt"))

    if vtt_files:
        return vtt_files[0]

    json3_files = list(directory.glob("*.json3"))

    if json3_files:
        return json3_files[0]

    return None


def fetch_captions(
    youtube_video_id: str,
) -> list[dict[str, Any]] | None:

    """
    Download YouTube subtitles using yt-dlp itself.

    Order:
        1. Manual subtitles
        2. Automatic subtitles

    """

    url = f"https://www.youtube.com/watch?v={youtube_video_id}"

    with tempfile.TemporaryDirectory(
        prefix=f"yt_caption_{youtube_video_id}_"
    ) as temp_dir:

        output_dir = Path(temp_dir)

        output_template = str(
            output_dir / f"{youtube_video_id}.%(ext)s"
        )

        ydl_opts: dict[str, Any] = {
            **base_ydl_opts(),

            "quiet": True,
            "verbose": True,
            "no_warnings": False,
            "skip_download": True,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitlesformat": "vtt",
            "subtitleslangs": [
                "en"
            ],
            "outtmpl": output_template,
            "ignoreerrors": False,
            "retries": 3,
            "fragment_retries": 3,
            "socket_timeout": 30,
        }

        logger.info(
            "Trying to download captions with yt-dlp for %s",
            youtube_video_id,
        )

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                with_backoff(
                    lambda: ydl.download([url])
                )

        except Exception as exc:
            raise_if_bot_check(exc)

            if isinstance(exc, RateLimitError) or looks_like_rate_limit(exc):
                raise RateLimitError(str(exc)) from exc

            logger.exception(
                "yt-dlp caption download failed for %s",
                youtube_video_id,
            )

            return None

        subtitle_file = _find_subtitle_file(output_dir)

        if subtitle_file is None:
            logger.info(
                "No subtitle file was downloaded for %s",
                youtube_video_id,
            )
            return None

        logger.info(
            "Subtitle file found for %s: %s",
            youtube_video_id,
            subtitle_file.name,
        )

        try:
            raw = subtitle_file.read_bytes()

            if subtitle_file.suffix.lower() == ".vtt":
                segments = _parse_vtt(raw)

            elif subtitle_file.suffix.lower() == ".json3":
                segments = _parse_json3(raw)

            else:
                logger.warning(
                    "Unsupported subtitle format for %s: %s",
                    youtube_video_id,
                    subtitle_file,
                )
                return None

        except Exception:
            logger.exception(
                "Failed to parse subtitle file for %s",
                youtube_video_id,
            )
            return None

        if not segments:
            logger.info(
                "Subtitle file was empty for %s",
                youtube_video_id,
            )
            return None

        logger.info(
            "Successfully fetched %d caption segments for %s",
            len(segments),
            youtube_video_id,
        )

        return segments
