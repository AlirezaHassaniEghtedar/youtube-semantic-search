import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yt_dlp

from app.config import settings
from app.services.retry import RateLimitError, looks_like_rate_limit, with_backoff

logger = logging.getLogger(__name__)


class BotCheckError(RateLimitError):
    """A YouTube sign-in / bot-check response from yt-dlp."""


_BOT_CHECK_MARKERS = ("sign in to confirm", "not a bot")


@dataclass
class ChannelListResult:
    """Flat channel entries plus metadata needed by the RSS fast path."""

    videos: list[dict[str, Any]]
    channel_id: str | None
    channel_name: str


def _base_ydl_opts() -> dict[str, Any]:
    """Shared pacing and optional authentication for yt-dlp requests."""
    browser = (
        settings.YT_COOKIES_FROM_BROWSER
        or settings.YT_DLP_COOKIES_FROM_BROWSER
    )
    opts: dict[str, Any] = {
        "sleep_interval_requests": 1,
        "sleep_interval": 2,
        "max_sleep_interval": 5,
    }
    if browser:
        opts["cookiesfrombrowser"] = (browser,)
    elif settings.YT_COOKIES_FILE:
        opts["cookiefile"] = settings.YT_COOKIES_FILE
    return opts


def _raise_if_bot_check(exc: Exception) -> None:
    if any(marker in str(exc).lower() for marker in _BOT_CHECK_MARKERS):
        raise BotCheckError(str(exc)) from exc


def _parse_upload_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.strptime(raw, "%Y%m%d")
        return dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _normalize_channel_url(url: str) -> str:
    url = url.strip().rstrip("/")
    if url.endswith(("/videos", "/streams", "/shorts")):
        return url
    return f"{url}/videos"


def list_channel_videos(
    url: str,
    max_items: int | None = None,
    start_item: int | None = None,
) -> ChannelListResult:
    """List videos on a channel via yt-dlp flat-playlist extraction."""
    url = _normalize_channel_url(url)

    ydl_opts: dict[str, Any] = {
        **_base_ydl_opts(),
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "ignoreerrors": True,
    }
    if max_items is not None:
        ydl_opts["playlistend"] = max_items
    if start_item and start_item > 1:
        ydl_opts["playliststart"] = start_item

    results: list[dict[str, Any]] = []
    channel_name = ""
    resolved_channel_id: str | None = None

    logger.info(
        "Fetching video list for channel: %s (start_item=%s, max_items=%s)",
        url,
        start_item,
        max_items,
    )

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = with_backoff(lambda: ydl.extract_info(url, download=False))
            if info is None:
                logger.error("yt-dlp returned no info for channel URL: %s", url)
                return ChannelListResult(results, None, channel_name)

            channel_name = info.get("channel") or info.get("uploader") or ""
            resolved_channel_id = info.get("channel_id")
            raw_entries = info.get("entries") or []

            entries: list[dict[str, Any]] = []
            for e in raw_entries:
                if e is None:
                    continue
                if e.get("entries"):
                    entries.extend(x for x in e.get("entries") if x)
                else:
                    entries.append(e)

            for entry in entries:
                video_id = entry.get("id") or entry.get("url", "").split("=")[-1]
                if not video_id or len(video_id) != 11:
                    continue

                title = entry.get("title") or "Untitled"
                upload_date = entry.get("upload_date") or entry.get("release_date")
                published_at = _parse_upload_date(upload_date)

                duration = entry.get("duration")
                if duration is None:
                    duration = entry.get("duration_string")

                duration_seconds: int | None = None
                if isinstance(duration, (int, float)):
                    duration_seconds = int(duration)

                results.append(
                    {
                        "youtube_video_id": video_id,
                        "title": title,
                        "published_at": published_at,
                        "duration_seconds": duration_seconds,
                        "channel_name": channel_name,
                    }
                )
    except Exception as exc:
        _raise_if_bot_check(exc)
        if isinstance(exc, RateLimitError) or looks_like_rate_limit(exc):
            raise RateLimitError(str(exc)) from exc
        logger.exception("Failed to fetch video list for channel URL: %s", url)
        raise

    logger.info("list_channel_videos: parsed %d videos for %s", len(results), url)
    return ChannelListResult(results, resolved_channel_id, channel_name)


def download_audio(youtube_video_id: str) -> Path:
    """Download audio for a video to downloads/{id}.m4a."""
    output_dir = settings.download_path
    output_template = str(output_dir / f"{youtube_video_id}.%(ext)s")

    ydl_opts: dict[str, Any] = {
        **_base_ydl_opts(),
        "quiet": True,
        "no_warnings": True,
        "format": "bestaudio/best",
        "outtmpl": output_template,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "m4a",
                "preferredquality": "192",
            }
        ],
        "ignoreerrors": False,
    }

    url = f"https://www.youtube.com/watch?v={youtube_video_id}"

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            with_backoff(lambda: ydl.download([url]))
    except Exception as exc:
        _raise_if_bot_check(exc)
        if isinstance(exc, RateLimitError) or looks_like_rate_limit(exc):
            raise RateLimitError(str(exc)) from exc
        raise

    expected = output_dir / f"{youtube_video_id}.m4a"
    if expected.exists():
        return expected

    for candidate in output_dir.glob(f"{youtube_video_id}.*"):
        if candidate.suffix in (".m4a", ".mp3", ".webm", ".opus"):
            return candidate

    raise FileNotFoundError(
        f"Audio download completed but file not found for {youtube_video_id}"
    )


def cleanup_audio_file(path: Path | str | None) -> None:
    if path is None:
        return
    p = Path(path)
    if p.exists():
        try:
            p.unlink()
        except OSError as exc:
            logger.warning("Failed to delete audio file %s: %s", p, exc)
