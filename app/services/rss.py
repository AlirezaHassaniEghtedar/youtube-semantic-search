import logging
from datetime import datetime
from typing import Any
from xml.etree import ElementTree

import httpx

logger = logging.getLogger(__name__)

_ATOM_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
}


def fetch_recent_videos_via_rss(
    youtube_channel_id: str,
) -> list[dict[str, Any]] | None:
    """Return YouTube's recent RSS entries with their real publish times.

    A failure returns ``None`` so callers can use their normal yt-dlp fallback.
    """
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={youtube_channel_id}"
    try:
        response = httpx.get(url, timeout=10.0, follow_redirects=True)
        response.raise_for_status()
        root = ElementTree.fromstring(response.text)
    except Exception:
        logger.exception("Failed to fetch/parse RSS feed for channel %s", youtube_channel_id)
        return None

    results: list[dict[str, Any]] = []
    for entry in root.findall("atom:entry", _ATOM_NS):
        video_id = entry.find("yt:videoId", _ATOM_NS)
        title = entry.find("atom:title", _ATOM_NS)
        published = entry.find("atom:published", _ATOM_NS)
        if video_id is None or not video_id.text or published is None or not published.text:
            continue
        try:
            published_at = datetime.fromisoformat(published.text.replace("Z", "+00:00"))
        except ValueError:
            logger.warning("Invalid RSS publication date for video %s", video_id.text)
            continue
        results.append(
            {
                "youtube_video_id": video_id.text,
                "title": title.text if title is not None and title.text else "Untitled",
                "published_at": published_at,
                "duration_seconds": None,
            }
        )

    logger.info("RSS feed returned %d entries for channel %s", len(results), youtube_channel_id)
    return results
