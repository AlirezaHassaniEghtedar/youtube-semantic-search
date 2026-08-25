import logging
from dataclasses import dataclass
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import yt_dlp
from dateutil import parser as date_parser

from app.config import settings
from app.services.retry import RateLimitError, looks_like_rate_limit, with_backoff
from app.services.youtube_pace import pace_youtube_request

logger = logging.getLogger(__name__)

NARROW_DATE_WINDOWS = frozenset({"24h", "7d", "30d", "custom_hours"})
_CHANNEL_ID_RE = re.compile(r"youtube\.com/channel/(UC[\w-]+)", re.I)
_ATOM_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
}


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


def _parse_rss_datetime(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = date_parser.isoparse(raw)
    except (ValueError, TypeError, OverflowError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _normalize_channel_url(url: str) -> str:
    url = url.strip().rstrip("/")
    if url.endswith(("/videos", "/streams", "/shorts")):
        return url
    return f"{url}/videos"


def _channel_id_from_url(url: str) -> str | None:
    match = _CHANNEL_ID_RE.search(url)
    return match.group(1) if match else None


def _video_record(
    video_id: str,
    title: str,
    published_at: datetime | None,
    duration_seconds: int | None,
    channel_name: str,
) -> dict[str, Any]:
    return {
        "youtube_video_id": video_id,
        "title": title,
        "published_at": published_at,
        "duration_seconds": duration_seconds,
        "channel_name": channel_name,
    }


def _resolve_channel_id(url: str) -> tuple[str, str]:
    """Return (channel_id, channel_name). Uses yt-dlp only when the URL has no UC id."""
    direct = _channel_id_from_url(url)
    if direct:
        return direct, ""

    url = _normalize_channel_url(url)
    ydl_opts: dict[str, Any] = {
        **_base_ydl_opts(),
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "ignoreerrors": True,
        "playlistend": 1,
    }
    pace_youtube_request("resolve_channel_id")
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = with_backoff(lambda: ydl.extract_info(url, download=False))
    except Exception as exc:
        _raise_if_bot_check(exc)
        if isinstance(exc, RateLimitError) or looks_like_rate_limit(exc):
            raise RateLimitError(str(exc)) from exc
        raise

    if not info:
        raise ValueError(f"yt-dlp returned no info while resolving channel id for {url}")

    channel_id = info.get("channel_id") or ""
    channel_name = info.get("channel") or info.get("uploader") or ""
    if not str(channel_id).startswith("UC"):
        uploader_id = str(info.get("uploader_id") or "")
        if uploader_id.startswith("UC"):
            channel_id = uploader_id
    if not str(channel_id).startswith("UC"):
        raise ValueError(f"Could not resolve channel_id from {url}")
    return str(channel_id), channel_name


def fetch_channel_rss_videos(url: str) -> list[dict[str, Any]] | None:
    """Fetch the latest ~15 uploads with real pubDate from YouTube's Atom RSS.

    Returns None when RSS cannot be used so the caller can fall back to
    extract_flat listing. Re-raises RateLimitError / bot-check so the pipeline
    can mark the channel ERROR instead of silently continuing.
    """
    try:
        channel_id, channel_name = _resolve_channel_id(url)
    except RateLimitError:
        raise
    except Exception:
        logger.exception("Failed to resolve channel_id for RSS listing: %s", url)
        return None

    feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    pace_youtube_request("channel_rss")
    try:
        response = httpx.get(feed_url, timeout=20.0, follow_redirects=True)
        response.raise_for_status()
        root = ET.fromstring(response.content)
    except Exception:
        logger.exception("RSS fetch failed for channel_id=%s url=%s", channel_id, url)
        return None

    if not channel_name:
        title_el = root.find("atom:title", _ATOM_NS)
        channel_name = (title_el.text or "").strip() if title_el is not None else ""

    results: list[dict[str, Any]] = []
    for entry in root.findall("atom:entry", _ATOM_NS):
        video_el = entry.find("yt:videoId", _ATOM_NS)
        video_id = (video_el.text or "").strip() if video_el is not None else ""
        if not video_id or len(video_id) != 11:
            continue
        title_el = entry.find("atom:title", _ATOM_NS)
        published_el = entry.find("atom:published", _ATOM_NS)
        title = (title_el.text or "Untitled").strip() if title_el is not None else "Untitled"
        published_at = _parse_rss_datetime(
            published_el.text if published_el is not None else None
        )
        results.append(
            _video_record(video_id, title, published_at, None, channel_name)
        )

    dated = sum(1 for item in results if item["published_at"] is not None)
    logger.info(
        "RSS listing for %s (channel_id=%s): %d entries, %d with pubDate, %d without",
        url,
        channel_id,
        len(results),
        dated,
        len(results) - dated,
    )
    if not results:
        logger.warning("RSS feed was empty for %s; falling back to flat playlist", url)
        return None
    return results


def merge_rss_dates(
    videos: list[dict[str, Any]], rss_videos: list[dict[str, Any]]
) -> int:
    """Copy RSS pubDate onto matching flat-playlist rows. Returns merge count."""
    by_id = {item["youtube_video_id"]: item for item in rss_videos}
    merged = 0
    for video in videos:
        rss = by_id.get(video["youtube_video_id"])
        if rss and rss.get("published_at") and not video.get("published_at"):
            video["published_at"] = rss["published_at"]
            if not video.get("channel_name") and rss.get("channel_name"):
                video["channel_name"] = rss["channel_name"]
            merged += 1
    return merged


def rss_covers_window(
    rss_videos: list[dict[str, Any]], window_start: datetime | None
) -> bool:
    """True when the oldest RSS pubDate is already older than the window start.

    YouTube RSS only exposes ~15 recent videos. If the oldest of those is still
    inside the window, newer-than-15 uploads may exist and we must also list
    via extract_flat. If the oldest is outside the window, RSS already covers
    every in-window upload and we can skip the heavier playlist crawl.
    """
    if window_start is None:
        return False
    dated = [item["published_at"] for item in rss_videos if item.get("published_at")]
    if not dated:
        return False
    return min(dated) < window_start


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
        pace_youtube_request("list_channel_videos")
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
                    _video_record(
                        video_id, title, published_at, duration_seconds, channel_name
                    )
                )
    except Exception as exc:
        _raise_if_bot_check(exc)
        if isinstance(exc, RateLimitError) or looks_like_rate_limit(exc):
            raise RateLimitError(str(exc)) from exc
        logger.exception("Failed to fetch video list for channel URL: %s", url)
        raise

    logger.info(
        "list_channel_videos: parsed %d videos for %s; upload_date present=%d null=%d",
        len(results),
        url,
        sum(1 for item in results if item["published_at"] is not None),
        sum(1 for item in results if item["published_at"] is None),
    )
    return results


def download_audio(youtube_video_id: str) -> Path:
    """Download audio for a video to downloads/{id}.m4a."""
    output_dir = settings.download_path
    output_template = str(output_dir / f"{youtube_video_id}.%(ext)s")

    ydl_opts: dict[str, Any] = {
        **_base_ydl_opts(),
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
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
        pace_youtube_request(f"download_audio:{youtube_video_id}")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # download() already extracts the formats it needs; do not call
            # extract_info() first — that would be a redundant YouTube round-trip.
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
