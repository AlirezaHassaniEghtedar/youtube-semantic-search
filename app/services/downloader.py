import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yt_dlp

from app.config import settings

logger = logging.getLogger(__name__)


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


def list_channel_videos(url: str, max_items: int | None = None) -> list[dict[str, Any]]:
    """List videos on a channel via yt-dlp flat-playlist extraction."""
    url = _normalize_channel_url(url)

    ydl_opts: dict[str, Any] = {
        "quiet": False,
        "no_warnings": False,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "ignoreerrors": True,
    }
    if max_items is not None:
        ydl_opts["playlistend"] = max_items

    results: list[dict[str, Any]] = []
    channel_name = ""

    logger.info("Fetching video list for channel: %s (max_items=%s)", url, max_items)

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if info is None:
                logger.error("yt-dlp returned no info for channel URL: %s", url)
                return results

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
    except Exception:
        logger.exception("Failed to fetch video list for channel URL: %s", url)
        raise

    logger.info("list_channel_videos: parsed %d videos for %s", len(results), url)
    return results


def download_audio(youtube_video_id: str) -> Path:
    """Download audio for a video to downloads/{id}.m4a."""
    output_dir = settings.download_path
    output_template = str(output_dir / f"{youtube_video_id}.%(ext)s")

    ydl_opts: dict[str, Any] = {
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

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

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
