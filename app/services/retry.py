import logging
import random
import time
from collections.abc import Callable
from typing import TypeVar

from app.services.live_detection import is_upcoming_event_error

logger = logging.getLogger(__name__)

T = TypeVar("T")

RATE_LIMIT_MARKERS = (
    "429",
    "403",
    "rate",
    "sign in to confirm",
    "not a bot",
    "too many requests",
    "block",
    "ip",
    "captcha",
    "unusual traffic",
)

RATE_LIMIT_EXCEPTION_NAMES = (
    "ipblocked",
    "requestblocked",
    "toomanyrequests",
    "youtuberequestfailed",
    "couldnotretrievetranscript",
)


class RateLimitError(Exception):
    """A request still appears rate-limited after bounded retries."""


def looks_like_rate_limit(exc: Exception) -> bool:
    # Scheduled streams are expected, not transient failures. In particular,
    # A caption extractor can use a broad exception class for them.
    if is_upcoming_event_error(exc):
        return False
    if type(exc).__name__.lower() in RATE_LIMIT_EXCEPTION_NAMES:
        return True
    return any(marker in str(exc).lower() for marker in RATE_LIMIT_MARKERS)


def with_backoff(
    fn: Callable[[], T], max_attempts: int = 3, base_delay: float = 5.0
) -> T:
    """Retry only likely transient YouTube rate-limit failures."""
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            if not looks_like_rate_limit(exc):
                raise
            if attempt == max_attempts:
                raise RateLimitError(str(exc)) from exc
            delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 2)
            logger.warning(
                "Possible YouTube rate limit (attempt %d/%d); backing off %.1fs: %s",
                attempt,
                max_attempts,
                delay,
                exc,
            )
            time.sleep(delay)
    raise last_exc or RuntimeError("Retry failed without an exception")
