"""Small, request-free detection helpers for scheduled YouTube events."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

_UPCOMING_EVENT_MARKERS = (
    "this live event will begin",
    "this live event begins",
    "premieres in",
    "scheduled for",
    "live stream that has not started",
    "live event has not started",
)


def is_upcoming_event_error(exc: Exception) -> bool:
    """Return whether a YouTube error says the video is scheduled for later."""
    message = str(exc).lower()
    return any(marker in message for marker in _UPCOMING_EVENT_MARKERS)


def parse_scheduled_start_from_error(exc: Exception) -> datetime | None:
    """Parse approximate scheduled_start_at from yt-dlp error messages.
    
    Handles phrases like:
    - "will begin in 12 hours"
    - "will begin in 2 days"
    - "premieres in 30 minutes"
    - "will begin in 1 hour 30 minutes"
    """
    message = str(exc).lower()
    
    # Match "will begin in X hours/days/minutes" or "premieres in X ..."
    # Handles variations like "will begin in 12 hours", "premieres in 2 days", etc.
    time_pattern = r"(?:will begin in|premieres in|begins in)\s*(?:(\d+)\s*(?:hour|hr)s?)?(?:\s*(?:and|,)?\s*)?(?:(\d+)\s*(?:minute|min)s?)?"
    
    match = re.search(time_pattern, message)
    if not match:
        return None
    
    hours = int(match.group(1)) if match.group(1) else 0
    minutes = int(match.group(2)) if match.group(2) else 0
    
    if hours == 0 and minutes == 0:
        return None
    
    try:
        now = datetime.now(timezone.utc)
        scheduled = now + timedelta(hours=hours, minutes=minutes)
        logger.info(
            "Parsed approximate scheduled_start_at from error message: "
            "+%d hours, +%d minutes → %s",
            hours,
            minutes,
            scheduled.isoformat(),
        )
        return scheduled
    except (ValueError, OverflowError):
        return None

