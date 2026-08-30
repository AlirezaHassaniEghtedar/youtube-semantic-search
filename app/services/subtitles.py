from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SUPPORTED_SUBTITLE_EXTENSIONS = {".srt", ".vtt"}
SUPPORTED_VIDEO_EXTENSIONS = {
    ".mp4", ".mkv", ".webm", ".avi", ".mov", ".m4v", ".flv",
}

_SRT_TIME_RE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})"
)


class SubtitleParseError(Exception):
    """Raised when a subtitle file cannot be parsed."""


def _timestamp_to_seconds(match: re.Match[str]) -> float:
    hours, minutes, seconds, millis = (int(g) for g in match.groups())
    return hours * 3600 + minutes * 60 + seconds + millis / 1000.0


def _parse_srt_or_vtt_blocks(raw_text: str) -> list[dict[str, Any]]:
    """Shared cue parser: both SRT and WebVTT use the same
    "HH:MM:SS,mmm --> HH:MM:SS,mmm" (or '.' separator) cue-timing line,
    followed by one or more lines of text, separated by blank lines.
    """
    # Normalize line endings and strip a leading "WEBVTT" header/BOM if present.
    text = raw_text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    if text.upper().startswith("WEBVTT"):
        text = text.split("\n", 1)[1] if "\n" in text else ""

    blocks = re.split(r"\n\s*\n", text.strip())
    segments: list[dict[str, Any]] = []

    for block in blocks:
        lines = [line for line in block.split("\n") if line.strip() != ""]
        if not lines:
            continue

        # Find the timing line within the block (skip numeric SRT indices
        # and any VTT cue identifiers that may precede it).
        timing_line_idx = None
        start_match = end_match = None
        for idx, line in enumerate(lines):
            if "-->" in line:
                parts = line.split("-->")
                if len(parts) == 2:
                    sm = _SRT_TIME_RE.search(parts[0])
                    em = _SRT_TIME_RE.search(parts[1])
                    if sm and em:
                        timing_line_idx = idx
                        start_match, end_match = sm, em
                        break
        if timing_line_idx is None or start_match is None or end_match is None:
            continue

        text_lines = lines[timing_line_idx + 1:]
        # Strip simple VTT/SRT inline formatting tags like <b>, <i>, <c.x>.
        cleaned = [re.sub(r"</?[a-zA-Z][^>]*>", "", line).strip() for line in text_lines]
        cue_text = " ".join(line for line in cleaned if line)
        if not cue_text:
            continue

        segments.append(
            {
                "start": _timestamp_to_seconds(start_match),
                "end": _timestamp_to_seconds(end_match),
                "text": cue_text,
            }
        )

    return segments


def parse_subtitle_file(path: Path) -> list[dict[str, Any]]:
    """Parse a .srt or .vtt file into [{start, end, text}, ...]."""
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUBTITLE_EXTENSIONS:
        raise SubtitleParseError(
            f"Unsupported subtitle format '{suffix}'. "
            f"Supported: {', '.join(sorted(SUPPORTED_SUBTITLE_EXTENSIONS))}"
        )
    if not path.exists():
        raise SubtitleParseError(f"Subtitle file not found: {path}")

    try:
        raw_text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        # Common for older Persian/Farsi-authored SRT files.
        raw_text = path.read_text(encoding="cp1256", errors="replace")

    segments = _parse_srt_or_vtt_blocks(raw_text)
    if not segments:
        raise SubtitleParseError(f"No cues could be parsed from {path.name}")

    logger.info("Parsed %d subtitle cues from %s", len(segments), path.name)
    return segments


def find_sibling_subtitle(video_path: Path) -> Path | None:
    """Look for a subtitle file with the same name as the video, next to it
    (e.g. 'movie.mp4' -> 'movie.srt' or 'movie.vtt'). Returns None if
    neither exists, so the caller can fall back to Whisper.
    """
    for ext in (".srt", ".vtt"):
        candidate = video_path.with_suffix(ext)
        if candidate.exists():
            return candidate
    return None


def probe_duration_seconds(video_path: Path) -> int | None:
    """Best-effort duration lookup via ffprobe (ffmpeg is already a required
    dependency of this project). Returns None if ffprobe is unavailable or
    the file can't be probed — duration is cosmetic, never fatal.
    """
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "json",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
        duration = float(data["format"]["duration"])
        return int(round(duration))
    except Exception:
        logger.debug("ffprobe duration lookup failed for %s", video_path, exc_info=True)
        return None


# Text-based subtitle codecs ffmpeg can convert straight to .srt. Bitmap/image
# subtitle formats (PGS, VobSub/dvd_subtitle) can't be losslessly converted to
# text and are skipped — there's no text to extract from a picture of text.
_TEXT_SUBTITLE_CODECS = {"subrip", "srt", "ass", "ssa", "mov_text", "webvtt"}
_PREFERRED_LANGUAGES = ["fa", "per", "fas", "en", "eng"]


def probe_embedded_subtitle_streams(video_path: Path) -> list[dict[str, Any]]:
    """List embedded subtitle streams in the video container, in the same
    0-based order ffmpeg's '-map 0:s:N' expects. Each entry:
    {"relative_index": int, "codec_name": str | None, "language": str | None}
    Returns [] if ffprobe is unavailable, the file has no subtitle streams,
    or probing fails for any reason — this is a "nice to have" check, never
    a hard requirement.
    """
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-select_streams", "s",
                "-show_entries", "stream=index,codec_name:stream_tags=language",
                "-of", "json",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            return []
        data = json.loads(result.stdout)
        streams = data.get("streams", [])
        return [
            {
                "relative_index": i,
                "codec_name": s.get("codec_name"),
                "language": (s.get("tags") or {}).get("language"),
            }
            for i, s in enumerate(streams)
        ]
    except Exception:
        logger.debug("ffprobe subtitle-stream probe failed for %s", video_path, exc_info=True)
        return []


def extract_embedded_subtitle(video_path: Path, output_dir: Path) -> Path | None:
    """Extract the best embedded subtitle track (if any) to a .srt file in
    output_dir. Returns None if there's no usable (text-based) subtitle
    track, or if ffmpeg fails to extract it — callers should treat that as
    "no embedded subtitle available" and fall back to the next option.
    """
    streams = probe_embedded_subtitle_streams(video_path)
    text_streams = [s for s in streams if s["codec_name"] in _TEXT_SUBTITLE_CODECS]
    if not text_streams:
        return None

    def _language_rank(stream: dict[str, Any]) -> int:
        lang = (stream["language"] or "").lower()
        return (
            _PREFERRED_LANGUAGES.index(lang)
            if lang in _PREFERRED_LANGUAGES
            else len(_PREFERRED_LANGUAGES)
        )

    chosen = min(text_streams, key=_language_rank)
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"{video_path.stem}.embedded.srt"

    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", str(video_path),
                "-map", f"0:s:{chosen['relative_index']}",
                "-c:s", "srt",
                str(out_path),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0 or not out_path.exists() or out_path.stat().st_size == 0:
            logger.info(
                "ffmpeg could not extract embedded subtitle from %s: %s",
                video_path.name, result.stderr[-500:] if result.stderr else "unknown error",
            )
            return None
        logger.info(
            "Extracted embedded subtitle track (lang=%s, codec=%s) from %s",
            chosen["language"], chosen["codec_name"], video_path.name,
        )
        return out_path
    except Exception:
        logger.debug("Embedded subtitle extraction failed for %s", video_path, exc_info=True)
        return None


def resolve_subtitle_segments(
    video_path: Path, scratch_dir: Path
) -> tuple[list[dict[str, Any]], str]:
    """Try, in order: an embedded subtitle track, then a same-named sibling
    file. Returns (segments, source) where source is "embedded", "sibling",
    or "whisper". segments is [] when source == "whisper" — that's a signal
    to the caller to run speech-to-text itself (this function doesn't do
    that, since it needs the already-loaded Whisper model instance).
    """
    embedded = extract_embedded_subtitle(video_path, scratch_dir)
    if embedded is not None:
        try:
            return parse_subtitle_file(embedded), "embedded"
        except SubtitleParseError:
            logger.info(
                "Extracted embedded subtitle from %s but couldn't parse it; "
                "falling back", video_path.name,
            )

    sibling = find_sibling_subtitle(video_path)
    if sibling is not None:
        return parse_subtitle_file(sibling), "sibling"

    return [], "whisper"