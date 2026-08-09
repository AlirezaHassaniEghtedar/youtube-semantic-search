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


def list_channel_videos(url: str) -> list[dict[str, Any]]:
    """List all videos on a channel via yt-dlp flat-playlist extraction.

    Make extraction more robust across different YouTube URL formats and
    yt-dlp entry shapes. Log counts for easier debugging when listings come
    back empty.
    """
    ydl_opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        # Use a boolean flat extraction which works for channels and playlists
        "extract_flat": True,
        "skip_download": True,
        "ignoreerrors": True,
    }

    results: list[dict[str, Any]] = []
    channel_name = ""

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        if not info:
            logger.debug("yt-dlp returned no info for URL: %s", url)
            return results

        channel_name = (
            info.get("channel")
            or info.get("uploader")
            or info.get("uploader_id")
            or ""
        )
        entries = info.get("entries") or []

        logger.debug("list_channel_videos: found %d entries for %s", len(entries), url)

        for entry in entries:
            if not entry:
                continue

            # Try multiple fields that may contain the video id or url
            vid_field = entry.get("id") or entry.get("url") or entry.get("webpage_url") or ""

            video_id = ""
            if isinstance(vid_field, str):
                video_id = vid_field
                # If it's a full URL, try to extract the video id
                if video_id.startswith("http"):
                    if "v=" in video_id:
                        video_id = video_id.split("v=")[-1].split("&")[0]
                    else:
                        video_id = video_id.rstrip("/").split("/")[-1]
                # Some extractors return a namespaced id like 'youtube:VIDEOID'
                if ":" in video_id and len(video_id) > 11:
                    video_id = video_id.split(":")[-1]

            if not video_id or len(video_id) != 11:
                # Skip entries that don't look like a YouTube id
                continue

            title = entry.get("title") or "Untitled"

            upload_date = entry.get("upload_date") or entry.get("release_date") or entry.get("timestamp")
            published_at = None
            if isinstance(upload_date, str):
                published_at = _parse_upload_date(upload_date)
            elif isinstance(upload_date, (int, float)):
                try:
                    published_at = datetime.fromtimestamp(int(upload_date), tz=timezone.utc)
                except Exception:
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
