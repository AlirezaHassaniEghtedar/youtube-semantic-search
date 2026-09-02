import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import yt_dlp
from dateutil import parser as date_parser

from app.config import settings
from app.services.retry import RateLimitError, looks_like_rate_limit, with_backoff
from app.services.ydl_common import BotCheckError, base_ydl_opts, raise_if_bot_check
from app.services.youtube_pace import pace_youtube_request
from app.services.live_detection import (
    is_upcoming_event_error,
    parse_scheduled_start_from_error,
)

logger = logging.getLogger(__name__)

NARROW_DATE_WINDOWS = frozenset({"24h", "7d", "30d", "custom_hours"})
_CHANNEL_ID_RE = re.compile(r"youtube\.com/channel/(UC[\w-]+)", re.I)
_ATOM_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
}

_UPCOMING_LIVE_STATUSES = frozenset({"is_upcoming", "upcoming"})
_STREAMED_LIVE_STATUSES = frozenset({"was_live", "post_live"})
_CHANNEL_TABS = ("videos", "shorts", "streams")
_TAB_MAX_ITEMS: dict[str, int] = {
    "shorts": 10,
    "videos": 50,
    "streams": 50,
}

# Backwards-compatible aliases for call sites in this module.
_base_ydl_opts = base_ydl_opts
_raise_if_bot_check = raise_if_bot_check


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


def _parse_release_datetime(
    release_timestamp: int | float | None, release_date: str | None
) -> datetime | None:
    if isinstance(release_timestamp, (int, float)):
        return datetime.fromtimestamp(release_timestamp, tz=timezone.utc)
    return _parse_upload_date(release_date)


def _normalize_live_status(value: Any) -> str:
    """Normalize yt-dlp's values to the statuses persisted by this app."""
    status = str(value or "").lower()
    if status in _UPCOMING_LIVE_STATUSES:
        return "upcoming"
    if status in _STREAMED_LIVE_STATUSES:
        return "was_live"
    if status == "is_live":
        return "is_live"
    return "none"


def classify_video_type(entry: dict[str, Any]) -> str:
    """Classify a listing entry.

    Priority: upcoming live_status → channel-tab origin (authoritative for
    /shorts and /streams) → /shorts/ URL → duration<=60 heuristic on the
    general /videos tab or API rows that have no tab signal. Flat entries
    usually lack width/height, so aspect ratio is not used.
    """
    live_status = _normalize_live_status(entry.get("live_status"))
    if live_status == "upcoming":
        return "upcoming event"

    source_tab = str(entry.get("source_tab") or "").lower()
    if source_tab == "shorts":
        return "short video"
    if source_tab == "streams":
        return "streamed video"

    if live_status == "was_live":
        return "streamed video"

    url = str(
        entry.get("webpage_url")
        or entry.get("original_url")
        or entry.get("url")
        or ""
    ).lower()
    if "/shorts/" in url:
        return "short video"

    duration = entry.get("duration_seconds", entry.get("duration"))
    # Best-effort only when the listing did not come from the Shorts tab.
    if isinstance(duration, (int, float)) and duration <= 60:
        return "short video"
    return "long video"


def _normalize_channel_url(url: str) -> str:
    url = url.strip().rstrip("/")
    if url.endswith(("/videos", "/streams", "/shorts")):
        return url
    return f"{url}/videos"


def _normalize_channel_base_url(url: str) -> str:
    """Strip a trailing /videos, /shorts, or /streams tab suffix, if present."""
    url = url.strip().rstrip("/")
    for suffix in ("/videos", "/streams", "/shorts"):
        if url.endswith(suffix):
            return url[: -len(suffix)]
    return url


def _missing_tab_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "does not have a" in message and "tab" in message


def _parse_entry_published_at(entry: dict[str, Any]) -> datetime | None:
    """Prefer yt-dlp approximate_date fields, then unix timestamps."""
    published_at = _parse_upload_date(
        entry.get("upload_date") or entry.get("release_date")
    )
    if published_at is not None:
        return published_at
    timestamp = entry.get("timestamp") or entry.get("release_timestamp")
    if timestamp is None:
        return None
    try:
        return datetime.fromtimestamp(float(timestamp), tz=timezone.utc)
    except (OSError, OverflowError, TypeError, ValueError):
        return None


def _scheduled_start_from_entry(entry: dict[str, Any]) -> datetime | None:
    return _parse_release_datetime(
        entry.get("release_timestamp"), entry.get("release_date")
    )


def _is_upcoming_entry(entry: dict[str, Any]) -> bool:
    if str(entry.get("live_status") or "").lower() in _UPCOMING_LIVE_STATUSES:
        return True
    scheduled = _scheduled_start_from_entry(entry)
    return scheduled is not None and scheduled > datetime.now(timezone.utc)


def _channel_id_from_url(url: str) -> str | None:
    match = _CHANNEL_ID_RE.search(url)
    return match.group(1) if match else None


def _video_record(
    video_id: str,
    title: str,
    published_at: datetime | None,
    duration_seconds: int | None,
    channel_name: str,
    *,
    live_status: str | None = None,
    scheduled_start_at: datetime | None = None,
    webpage_url: str | None = None,
    source_tab: str | None = None,
) -> dict[str, Any]:
    return {
        "youtube_video_id": video_id,
        "title": title,
        "published_at": published_at,
        "duration_seconds": duration_seconds,
        "channel_name": channel_name,
        "live_status": _normalize_live_status(live_status),
        "scheduled_start_at": scheduled_start_at,
        "webpage_url": webpage_url,
        "source_tab": source_tab,
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
    
    # Retry RSS fetch with simple backoff (2 attempts, 1-2 second delay)
    root = None
    last_error = None
    for attempt in range(1, 3):  # 2 attempts
        pace_youtube_request("channel_rss")
        try:
            response = httpx.get(feed_url, timeout=20.0, follow_redirects=True)
            response.raise_for_status()
            root = ET.fromstring(response.content)
            break  # Success, exit retry loop
        except Exception as exc:
            last_error = exc
            if attempt < 2:  # Not the last attempt
                logger.debug(
                    "RSS fetch attempt %d failed for channel_id=%s; retrying in 1-2 seconds (%s)",
                    attempt, channel_id, type(exc).__name__
                )
                time.sleep(1.5)
            # Otherwise continue to next attempt or fall through to error logging
    
    if root is None:
        logger.exception(
            "RSS fetch failed after retries for channel_id=%s (last error: %s)",
            channel_id, last_error
        )
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


def merge_rss_and_flat(
    flat_videos: list[dict[str, Any]], rss_videos: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Merge RSS and flat entries, retaining flat-only and RSS-only videos.

    RSS provides the more precise publication time but no duration. Flat yt-dlp
    entries supply duration and other metadata, so matching records combine both
    sources while RSS-only and flat-only records are retained unchanged.
    """
    rss_by_id = {item["youtube_video_id"]: item for item in rss_videos}
    merged: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for flat in flat_videos:
        video = dict(flat)
        video_id = video["youtube_video_id"]
        seen_ids.add(video_id)
        rss = rss_by_id.get(video_id)
        if rss:
            if rss.get("published_at") is not None:
                video["published_at"] = rss["published_at"]
            video["title"] = rss.get("title") or video.get("title") or "Untitled"
            video["channel_name"] = (
                video.get("channel_name") or rss.get("channel_name") or ""
            )
        merged.append(video)

    merged.extend(dict(rss) for rss in rss_videos if rss["youtube_video_id"] not in seen_ids)
    return merged


def list_channel_videos(
    url: str,
    max_items: int | None = None,
    start_item: int | None = None,
) -> list[dict[str, Any]]:
    """List a channel's videos across its videos/shorts/streams tabs."""
    base_url = _normalize_channel_base_url(url)
    results: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    channel_name = ""

    for tab in _CHANNEL_TABS:
        tab_url = f"{base_url}/{tab}"
        tab_cap = _TAB_MAX_ITEMS[tab]
        tab_max_items = tab_cap if max_items is None else min(max_items, tab_cap)
        try:
            tab_results, tab_channel_name = _list_channel_tab(
                tab_url, tab, tab_max_items, start_item
            )
        except (RateLimitError, BotCheckError):
            raise
        except Exception as exc:
            if _missing_tab_error(exc):
                logger.info("Channel %s has no %s tab; skipping", base_url, tab)
                continue
            raise

        channel_name = channel_name or tab_channel_name
        for item in tab_results:
            if item["youtube_video_id"] in seen_ids:
                continue
            seen_ids.add(item["youtube_video_id"])
            results.append(item)

    for item in results:
        item["channel_name"] = channel_name or item.get("channel_name")

    logger.info(
        "list_channel_videos: parsed %d videos total for %s "
        "(videos+shorts+streams); upload_date present=%d null=%d",
        len(results),
        base_url,
        sum(1 for item in results if item["published_at"] is not None),
        sum(1 for item in results if item["published_at"] is None),
    )
    return results


def _list_channel_tab(
    url: str,
    tab: str,
    max_items: int | None,
    start_item: int | None,
) -> tuple[list[dict[str, Any]], str]:
    """List entries from a single channel tab (videos, shorts, or streams)."""
    ydl_opts: dict[str, Any] = {
        **_base_ydl_opts(),
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "ignoreerrors": True,
    }
    ydl_opts["extractor_args"] = {
        **ydl_opts.get("extractor_args", {}),
        "youtubetab": {"approximate_date": ["true"]},
    }
    if max_items is not None:
        ydl_opts["playlistend"] = max_items
    if start_item and start_item > 1:
        ydl_opts["playliststart"] = start_item

    results: list[dict[str, Any]] = []
    channel_name = ""
    logger.info(
        "Fetching video list for channel tab: %s (start_item=%s, max_items=%s)",
        url,
        start_item,
        max_items,
    )

    try:
        pace_youtube_request(f"list_channel_tab:{tab}")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = with_backoff(lambda: ydl.extract_info(url, download=False))
            if info is None:
                logger.error("yt-dlp returned no info for channel URL: %s", url)
                return results, channel_name

            channel_name = info.get("channel") or info.get("uploader") or ""
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

                upcoming = _is_upcoming_entry(entry)
                live_status = "is_upcoming" if upcoming else entry.get("live_status")
                if upcoming:
                    logger.info(
                        "Keeping upcoming livestream %s (%s) as UPCOMING_EVENT metadata",
                        video_id,
                        entry.get("title"),
                    )

                duration = entry.get("duration")
                duration_seconds = int(duration) if isinstance(duration, (int, float)) else None
                if duration_seconds is None and entry.get("duration_string"):
                    parsed_duration = yt_dlp.utils.parse_duration(entry["duration_string"])
                    duration_seconds = (
                        int(parsed_duration) if parsed_duration is not None else None
                    )

                results.append(
                    _video_record(
                        video_id,
                        entry.get("title") or "Untitled",
                        _parse_entry_published_at(entry),
                        duration_seconds,
                        channel_name,
                        live_status=live_status,
                        scheduled_start_at=_scheduled_start_from_entry(entry),
                        webpage_url=entry.get("webpage_url") or entry.get("original_url"),
                        source_tab=tab,
                    )
                )
    except Exception as exc:
        _raise_if_bot_check(exc)
        if isinstance(exc, RateLimitError) or looks_like_rate_limit(exc):
            raise RateLimitError(str(exc)) from exc
        if _missing_tab_error(exc):
            raise
        logger.exception("Failed to fetch video list for channel tab: %s", url)
        raise

    logger.info("_list_channel_tab: parsed %d videos for %s", len(results), url)
    return results, channel_name


def fetch_video_live_metadata(youtube_video_id: str) -> dict[str, Any]:
    """Fetch full metadata for one video after an upcoming-event response.
    
    For upcoming/unplayable videos, tries:
    1. Extract metadata with ignore_no_formats_error to get release_timestamp
    2. If that fails, parse the error message for relative time (e.g. "will begin in 12 hours")
    3. Fallback to None if both fail
    """
    url = f"https://www.youtube.com/watch?v={youtube_video_id}"
    ydl_opts: dict[str, Any] = {
        **_base_ydl_opts(),
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
        "ignore_no_formats_error": True,
    }
    
    scheduled_start_at = None
    info: dict[str, Any] | None = None
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            def extract() -> dict[str, Any]:
                pace_youtube_request(f"live_metadata:{youtube_video_id}")
                return ydl.extract_info(url, download=False, process=False) or {}

            info = with_backoff(extract)
            
            # Try to get scheduled start from structured metadata
            if info:
                scheduled_start_at = _parse_release_datetime(
                    info.get("release_timestamp"), info.get("release_date")
                )
                if scheduled_start_at:
                    logger.info(
                        "Extracted precise scheduled_start_at from metadata for %s: %s",
                        youtube_video_id,
                        scheduled_start_at.isoformat(),
                    )
    except Exception as exc:
        _raise_if_bot_check(exc)
        if isinstance(exc, RateLimitError) or looks_like_rate_limit(exc):
            raise RateLimitError(str(exc)) from exc
        
        # If upcoming event error, try fallback parsing from error message
        if is_upcoming_event_error(exc):
            scheduled_start_at = parse_scheduled_start_from_error(exc)
            if not scheduled_start_at:
                logger.warning(
                    "Could not parse scheduled time from error for %s: %s",
                    youtube_video_id,
                    exc,
                )
        else:
            raise

    return {
        "title": (info or {}).get("title") or "",
        "live_status": _normalize_live_status((info or {}).get("live_status")),
        "scheduled_start_at": scheduled_start_at,
    }


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
