"""YouTube Data API v3 integration for full channel video metadata."""

import logging
import re
from datetime import datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_QUOTA_EXHAUSTED_REASONS = frozenset({"quotaExceeded", "dailyLimitExceeded"})


class YouTubeAPIError(Exception):
    """Base exception for YouTube API errors."""
    pass


class QuotaExhaustedError(YouTubeAPIError):
    """Raised when the daily API quota is exhausted."""
    pass


def _parse_iso8601_duration(duration_str: str) -> int | None:
    """Parse ISO 8601 duration string (e.g. 'PT1H2M3S') into total seconds.
    
    Handles: PT1H2M3S, PT5M30S, PT45S, P1DT2H3M4S, etc.
    """
    if not duration_str or not duration_str.startswith("PT"):
        return None
    
    # Remove PT prefix
    duration_str = duration_str[2:]
    
    pattern = r"(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:([\d.]+)S)?)?"
    match = re.match(pattern, duration_str)
    if not match:
        return None
    
    days, hours, minutes, seconds = match.groups()
    
    total_seconds = 0
    if days:
        total_seconds += int(days) * 86400
    if hours:
        total_seconds += int(hours) * 3600
    if minutes:
        total_seconds += int(minutes) * 60
    if seconds:
        total_seconds += int(float(seconds))
    
    return total_seconds if total_seconds > 0 else None


def _check_quota_error(response_data: dict[str, Any]) -> None:
    """Check if response indicates quota exhaustion; raise QuotaExhaustedError if so."""
    if "error" in response_data:
        error = response_data["error"]
        if isinstance(error, dict):
            reason = error.get("errors", [{}])[0].get("reason", "")
            if reason in _QUOTA_EXHAUSTED_REASONS:
                raise QuotaExhaustedError(
                    f"YouTube Data API quota exceeded: {reason}"
                )


def _make_request(
    url: str, params: dict[str, Any], api_key: str, timeout: float = 10.0
) -> dict[str, Any]:
    """Make a single HTTP request to YouTube API with error handling.
    
    Raises QuotaExhaustedError if quota is exceeded.
    Raises YouTubeAPIError for other API errors.
    Raises httpx.HTTPError for network errors.
    """
    params["key"] = api_key
    
    response = httpx.get(url, params=params, timeout=timeout, follow_redirects=True)
    response.raise_for_status()
    data = response.json()
    
    _check_quota_error(data)
    
    if "error" in data:
        error = data["error"]
        raise YouTubeAPIError(f"YouTube API error: {error}")
    
    return data


def resolve_uploads_playlist_id(channel_id: str, api_key: str) -> str | None:
    """Resolve the uploads playlist ID for a channel.
    
    Returns the contentDetails.relatedPlaylists.uploads ID, or None on error.
    This playlist contains ALL videos uploaded by the channel.
    
    API call: channels.list?part=contentDetails&id={channel_id}
    Quota cost: 1 unit
    """
    try:
        data = _make_request(
            "https://www.googleapis.com/youtube/v3/channels",
            {"part": "contentDetails", "id": channel_id},
            api_key,
        )
        
        items = data.get("items", [])
        if not items:
            logger.warning("No channel found for id=%s via API", channel_id)
            return None
        
        uploads_id = items[0].get("contentDetails", {}).get(
            "relatedPlaylists", {}
        ).get("uploads")
        
        if uploads_id:
            logger.info("Resolved uploads playlist id=%s for channel %s", uploads_id, channel_id)
        else:
            logger.warning("No uploads playlist found for channel %s", channel_id)
        
        return uploads_id
    except QuotaExhaustedError:
        raise
    except Exception as exc:
        logger.exception("Failed to resolve uploads playlist for channel %s: %s", channel_id, exc)
        return None


def fetch_channel_videos_via_api(
    channel_id: str,
    api_key: str,
    published_after: datetime | None = None,
    max_items: int | None = None,
) -> list[dict[str, Any]]:
    """Fetch videos from a channel's uploads playlist via YouTube API.
    
    Paginates playlistItems.list to collect all (or up to max_items) videos.
    Returns list of dicts with: videoId, title, publishedAt (ISO string).
    
    Quota cost: 1 unit per API call (plus 1 for resolve_uploads_playlist_id).
    Raises QuotaExhaustedError if quota is exhausted during pagination.
    """
    uploads_id = resolve_uploads_playlist_id(channel_id, api_key)
    if not uploads_id:
        logger.warning("Cannot fetch videos for channel %s: no uploads playlist", channel_id)
        return []
    
    videos: list[dict[str, Any]] = []
    page_token = None
    
    try:
        while True:
            params: dict[str, Any] = {
                "part": "snippet,contentDetails",
                "playlistId": uploads_id,
                "maxResults": 50,  # API max per page
            }
            if page_token:
                params["pageToken"] = page_token
            if published_after:
                # publishedAfter is ISO 8601 format; compare to stop early
                params["publishedAfter"] = published_after.isoformat()
            
            data = _make_request(
                "https://www.googleapis.com/youtube/v3/playlistItems",
                params,
                api_key,
            )
            
            items = data.get("items", [])
            for item in items:
                snippet = item.get("snippet", {})
                content = item.get("contentDetails", {})
                
                video_id = content.get("videoId")
                title = snippet.get("title", "")
                # Use contentDetails.videoPublishedAt (actual publish time, not playlist-add time)
                pub_at_str = content.get("videoPublishedAt")
                
                if not video_id:
                    continue
                
                try:
                    published_at = None
                    if pub_at_str:
                        published_at = datetime.fromisoformat(pub_at_str.replace("Z", "+00:00"))
                    
                    # Early exit: if published_after is set and this item is older, stop
                    if published_after and published_at and published_at < published_after:
                        logger.info(
                            "Stopping API pagination: reached videos published before cutoff %s",
                            published_after.isoformat(),
                        )
                        return videos
                    
                    videos.append({
                        "videoId": video_id,
                        "title": title,
                        "publishedAt": pub_at_str,
                    })
                    
                    if max_items and len(videos) >= max_items:
                        logger.info("Reached max_items=%d; stopping pagination", max_items)
                        return videos[:max_items]
                except Exception as e:
                    logger.warning("Failed to parse video item %s: %s", video_id, e)
                    continue
            
            # Check for next page
            page_token = data.get("nextPageToken")
            if not page_token:
                break
    except QuotaExhaustedError:
        logger.warning(
            "YouTube API quota exhausted after fetching %d videos for channel %s",
            len(videos),
            channel_id,
        )
        raise
    
    logger.info("Fetched %d videos via API for channel %s", len(videos), channel_id)
    return videos


def fetch_video_durations_via_api(
    video_ids: list[str], api_key: str
) -> dict[str, int]:
    """Fetch duration in seconds for multiple videos via YouTube API.
    
    Batches video IDs in groups of 50 (API limit) and calls videos.list.
    Returns dict mapping videoId -> duration_seconds.
    
    Quota cost: 1 unit per batch (up to 50 videos per batch).
    Raises QuotaExhaustedError if quota is exhausted.
    """
    if not video_ids:
        return {}
    
    durations: dict[str, int] = {}
    
    # Batch in groups of 50 (YouTube API limit)
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i : i + 50]
        
        try:
            data = _make_request(
                "https://www.googleapis.com/youtube/v3/videos",
                {
                    "part": "contentDetails",
                    "id": ",".join(batch),
                },
                api_key,
            )
            
            for item in data.get("items", []):
                video_id = item.get("id")
                duration_str = item.get("contentDetails", {}).get("duration")
                
                if not video_id or not duration_str:
                    continue
                
                duration_seconds = _parse_iso8601_duration(duration_str)
                if duration_seconds is not None:
                    durations[video_id] = duration_seconds
        except QuotaExhaustedError:
            logger.warning(
                "YouTube API quota exhausted after fetching %d/%d video durations",
                len(durations),
                len(video_ids),
            )
            raise
        except Exception as exc:
            logger.warning("Failed to fetch durations for batch %s: %s", batch, exc)
            continue
    
    logger.info(
        "Fetched durations for %d/%d videos via API",
        len(durations),
        len(video_ids),
    )
    return durations


def fetch_live_status_via_api(
    video_ids: list[str], api_key: str
) -> dict[str, dict[str, Any]]:
    """Fetch live streaming details for multiple videos.
    
    Batches video IDs in groups of 50 and calls videos.list with
    liveStreamingDetails and snippet parts.
    
    Returns dict mapping videoId -> {liveStreamingDetails, snippet fields}.
    - liveStreamingDetails.scheduledStartTime (ISO 8601) for upcoming streams
    - snippet.liveBroadcastContent ('none', 'upcoming', 'live')
    
    Quota cost: 1 unit per batch (up to 50 videos per batch).
    Raises QuotaExhaustedError if quota is exhausted.
    """
    if not video_ids:
        return {}
    
    live_statuses: dict[str, dict[str, Any]] = {}
    
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i : i + 50]
        
        try:
            data = _make_request(
                "https://www.googleapis.com/youtube/v3/videos",
                {
                    "part": "liveStreamingDetails,snippet",
                    "id": ",".join(batch),
                },
                api_key,
            )
            
            for item in data.get("items", []):
                video_id = item.get("id")
                if not video_id:
                    continue
                
                live_statuses[video_id] = {
                    "liveStreamingDetails": item.get("liveStreamingDetails", {}),
                    "liveBroadcastContent": item.get("snippet", {}).get("liveBroadcastContent"),
                }
        except QuotaExhaustedError:
            logger.warning(
                "YouTube API quota exhausted after fetching live status for %d/%d videos",
                len(live_statuses),
                len(video_ids),
            )
            raise
        except Exception as exc:
            logger.warning("Failed to fetch live status for batch %s: %s", batch, exc)
            continue
    
    logger.info(
        "Fetched live status for %d/%d videos via API",
        len(live_statuses),
        len(video_ids),
    )
    return live_statuses
