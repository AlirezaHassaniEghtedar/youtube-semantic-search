import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yt_dlp

from app.config import settings
from app.services.retry import RateLimitError, looks_like_rate_limit, with_backoff
from app.services.ydl_common import BotCheckError, base_ydl_opts, raise_if_bot_check

logger = logging.getLogger(__name__)

# Backwards-compatible aliases (kept private-style names used elsewhere in
# this module) so the rest of this file does not need to change.
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


_CHANNEL_TABS = ("videos", "shorts", "streams")
SHORTS_TAB_MAX_ITEMS = 10
OTHER_TABS_MAX_ITEMS = 50

_TAB_MAX_ITEMS: dict[str, int] = {
    "shorts": SHORTS_TAB_MAX_ITEMS,
    "videos": OTHER_TABS_MAX_ITEMS,
    "streams": OTHER_TABS_MAX_ITEMS,
}

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
        tab_cap = _TAB_MAX_ITEMS.get(tab)
        tab_max_items = (
            tab_cap if max_items is None else min(max_items, tab_cap)
        )
        try:
            tab_results, tab_channel_name = _list_channel_tab(
                tab_url, tab_max_items, start_item
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
        "list_channel_videos: parsed %d videos total for %s (videos+shorts+streams)",
        len(results),
        base_url,
    )
    return results

def _list_channel_tab(
    url: str,
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

                if entry.get("live_status") == "is_upcoming":
                    logger.info(
                        "Skipping upcoming livestream %s (%s): not started yet",
                        video_id,
                        entry.get("title"),
                    )
                    continue

                release_timestamp = entry.get("release_timestamp")
                if release_timestamp is not None:
                    try:
                        release_dt = datetime.fromtimestamp(
                            float(release_timestamp), tz=timezone.utc
                        )
                        if release_dt > datetime.now(timezone.utc):
                            logger.info(
                                "Skipping scheduled livestream %s (%s): "
                                "release_timestamp is in the future",
                                video_id,
                                entry.get("title"),
                            )
                            continue
                    except (OSError, OverflowError, TypeError, ValueError):
                        pass

                title = entry.get("title") or "Untitled"
                upload_date = entry.get("upload_date") or entry.get("release_date")
                published_at = _parse_upload_date(upload_date)
                if published_at is None:
                    timestamp = entry.get("timestamp") or entry.get("release_timestamp")
                    if timestamp is not None:
                        try:
                            published_at = datetime.fromtimestamp(
                                float(timestamp), tz=timezone.utc
                            )
                        except (OSError, OverflowError, TypeError, ValueError):
                            published_at = None

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
        logger.exception("Failed to fetch video list for channel tab: %s", url)
        raise

    logger.info("_list_channel_tab: parsed %d videos for %s", len(results), url)
    return results, channel_name

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
