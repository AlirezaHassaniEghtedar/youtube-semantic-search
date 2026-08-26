"""Small, request-free detection helpers for scheduled YouTube events."""

from __future__ import annotations


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
