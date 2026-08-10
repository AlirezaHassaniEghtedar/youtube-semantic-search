import asyncio
import time


class GlobalRateLimiter:
    """Serialize yt-dlp starts so concurrent jobs do not create request bursts."""

    def __init__(self, min_interval_seconds: float) -> None:
        self._min_interval = min_interval_seconds
        self._lock = asyncio.Lock()
        self._last_call_at = 0.0

    @property
    def min_interval(self) -> float:
        return self._min_interval

    async def wait(self) -> None:
        async with self._lock:
            elapsed = time.monotonic() - self._last_call_at
            if elapsed < self._min_interval:
                await asyncio.sleep(self._min_interval - elapsed)
            self._last_call_at = time.monotonic()

    def increase_interval(self, factor: float = 2.0, max_seconds: float = 30.0) -> None:
        self._min_interval = min(self._min_interval * factor, max_seconds)
