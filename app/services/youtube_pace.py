"""Process-wide pacing for outbound YouTube requests.

Per-stage semaphores and jitter still apply, but listing, captions, RSS, and
audio downloads all share one minimum interval so the combined request rate
stays bounded.
"""

from __future__ import annotations

import logging
import threading
import time

from app.config import settings

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_last_request_at = 0.0


def pace_youtube_request(reason: str) -> None:
    """Block until the global minimum interval since the last YouTube call."""
    global _last_request_at
    interval = settings.YT_GLOBAL_MIN_INTERVAL_SECONDS
    if interval <= 0:
        return
    with _lock:
        now = time.monotonic()
        wait = interval - (now - _last_request_at)
        if wait > 0:
            logger.debug("YouTube global pacing: sleeping %.2fs before %s", wait, reason)
            time.sleep(wait)
        _last_request_at = time.monotonic()
