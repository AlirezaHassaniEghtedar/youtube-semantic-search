"""Shared helpers for building yt-dlp options and detecting bot-check blocks.

Every yt-dlp call site (audio downloads, channel listing, caption fetching)
uses these options so cookies, PO-token extractor args, and sleep intervals
stay consistent.
"""

from typing import Any

from app.config import settings
from app.services.retry import RateLimitError


class BotCheckError(RateLimitError):
    """A YouTube sign-in / bot-check response from yt-dlp."""


_BOT_CHECK_MARKERS = ("sign in to confirm", "not a bot")


def base_ydl_opts() -> dict[str, Any]:
    browser = (
        settings.YT_COOKIES_FROM_BROWSER
        or settings.YT_DLP_COOKIES_FROM_BROWSER
    )
    opts: dict[str, Any] = {
        "sleep_interval_requests": 1,
        "sleep_interval": 2,
        "max_sleep_interval": 5,
        "remote_components": ["ejs:github"],
        "extractor_args": {
            "youtubepot-bgutilhttp": {
                "base_url": settings.YT_POT_PROVIDER_BASE_URL,
            }
        },
    }
    if browser:
        opts["cookiesfrombrowser"] = (browser,)
    elif settings.YT_COOKIES_FILE:
        opts["cookiefile"] = settings.YT_COOKIES_FILE
    return opts


def raise_if_bot_check(exc: Exception) -> None:
    if any(marker in str(exc).lower() for marker in _BOT_CHECK_MARKERS):
        raise BotCheckError(str(exc)) from exc
