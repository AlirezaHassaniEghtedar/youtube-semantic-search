"""Backfill/reclassify videos synced before the "completed broadcast" fix.

Background: the YouTube Data API path in app/services/pipeline.py used to map
liveBroadcastContent only 'upcoming'/'live' — 'completed' fell through to
'none'. classify_video_type() then saw live_status='none' and labeled completed
livestreams as "long video" instead of "streamed video". The stored live_status
for those rows is therefore ALSO wrong ('none'), so a pure in-DB recompute is
not enough: this script re-fetches live status from the YouTube Data API
(videos.list, batched 50 IDs per call, 1 quota unit per batch) before
reclassifying.

What it does:
  1. Loads all Video rows.
  2. Skips rows that cannot depend on live status at all:
     - video_type 'short video' / 'upcoming event' (duration- or
       tab-derived / already correct)
     - local (non-YouTube) videos (source_type='local')
     - rows whose stored live_status is already a real non-'none' value
       ('upcoming'/'is_live'/'was_live'): those were persisted correctly, so
       no API call is needed — they are only reclassified locally (no-op when
       already correct, which keeps the script idempotent).
  3. For the remaining candidates, re-fetches live status via
     fetch_live_status_via_api (the same helper the pipeline uses) and stores
     it when the API reports a real broadcast state.
  4. Re-runs classify_video_type() from stored title/duration/live_status/
     scheduled_start_at and updates ONLY the video_type (and, where the API
     provided a real value, live_status) columns. No other field is touched.

Safety:
  - Idempotent: re-running updates nothing (already-fixed rows skip the API
    call; classify_video_type() is deterministic on the same inputs).
  - Quota: a QuotaExhaustedError aborts further API calls gracefully with a
    clear log message (same handling as the pipeline — no new retry logic).
  - Dry run: pass --dry-run to see what would change without writing.

Usage (from the project root):
    python scripts/reclassify_videos.py [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Allow running as a plain script from the project root.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import select  # noqa: E402

from app.database import async_session_factory  # noqa: E402
from app.models import Video  # noqa: E402
from app.services.downloader import classify_video_type  # noqa: E402
from app.services.youtube_api import (  # noqa: E402
    QuotaExhaustedError,
    fetch_live_status_via_api,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("reclassify_videos")

# Video types whose classification does not depend on live status:
# 'short video' is duration/tab-derived, 'upcoming event' is already correct.
SKIP_VIDEO_TYPES = {"short video", "upcoming event"}
# Stored live_status values that are already trustworthy (no API call needed).
TRUSTED_LIVE_STATUSES = {"upcoming", "is_live", "was_live"}

# Mirror of the pipeline's liveBroadcastContent normalization
# (app/services/pipeline.py::_fetch_videos_via_youtube_api).
_BROADCAST_CONTENT_TO_LIVE_STATUS = {
    "upcoming": "upcoming",
    "live": "is_live",
    "completed": "was_live",
}


def _derive_live_status(api_info: dict) -> str | None:
    """Derive the app's normalized live status from one videos.list item.

    liveBroadcastContent is mapped exactly like the pipeline does. Note that
    YouTube flips a completed broadcast from 'completed' back to 'none'
    shortly after it ends, but liveStreamingDetails.actualEndTime keeps
    proving forever that the video WAS a broadcast — so that field is the
    durable signal for 'was_live'.
    """
    broadcast = api_info.get("liveBroadcastContent")
    mapped = _BROADCAST_CONTENT_TO_LIVE_STATUS.get(broadcast)
    if mapped:
        return mapped
    details = api_info.get("liveStreamingDetails") or {}
    if details.get("actualEndTime"):
        # broadcast is 'none' (or missing) but the stream demonstrably ran.
        return "was_live"
    return None


async def reclassify(dry_run: bool = False) -> None:
    async with async_session_factory() as session:
        result = await session.execute(select(Video))
        videos = list(result.scalars())

        candidates: list[Video] = []
        for video in videos:
            if video.source_type == "local":
                continue
            if video.video_type in SKIP_VIDEO_TYPES:
                continue
            candidates.append(video)

        need_api = [
            v
            for v in candidates
            if not v.live_status or v.live_status not in TRUSTED_LIVE_STATUSES
        ]

        logger.info(
            "Checked %d video rows total; %d classification candidates, "
            "%d need a live-status API re-fetch (%d skipped as already "
            "correct/local).",
            len(videos),
            len(candidates),
            len(need_api),
            len(videos) - len(candidates),
        )

        live_statuses: dict[str, dict] = {}
        if need_api:
            ids = [v.youtube_video_id for v in need_api]
            try:
                # fetch_live_status_via_api batches 50 IDs per videos.list
                # call (1 quota unit per batch) and raises QuotaExhaustedError.
                live_statuses = fetch_live_status_via_api(ids, _api_key())
            except QuotaExhaustedError as exc:
                logger.warning(
                    "YouTube Data API quota exhausted (%s); proceeding with "
                    "stored data only — re-run this script once quota resets "
                    "to finish the backfill.",
                    exc,
                )

        changed_type = 0
        changed_live = 0
        for video in candidates:
            api_info = live_statuses.get(video.youtube_video_id)
            new_live_status = video.live_status

            if api_info is not None:
                derived = _derive_live_status(api_info)
                if derived and derived != video.live_status:
                    new_live_status = derived

            entry = {
                "title": video.title,
                "duration_seconds": video.duration_seconds,
                "live_status": new_live_status,
                "scheduled_start_at": video.scheduled_start_at,
            }
            new_type = classify_video_type(entry)

            if new_type != video.video_type:
                logger.info(
                    "Reclassifying %s (%s): video_type %r -> %r",
                    video.youtube_video_id,
                    video.title[:60],
                    video.video_type,
                    new_type,
                )
                changed_type += 1
            if new_live_status != video.live_status:
                logger.info(
                    "Live status %s: %r -> %r",
                    video.youtube_video_id,
                    video.live_status,
                    new_live_status,
                )
                changed_live += 1

            if not dry_run:
                # Targeted correction: video_type (and live_status, the
                # mis-captured input) only — never title/duration/dates/status.
                video.video_type = new_type
                video.live_status = new_live_status

        if dry_run:
            await session.rollback()
            logger.info(
                "DRY RUN complete: %d video_type change(s), %d live_status "
                "change(s) — nothing written.",
                changed_type,
                changed_live,
            )
        else:
            await session.commit()
            logger.info(
                "Backfill complete: %d row(s) checked, %d video_type "
                "corrected, %d live_status corrected.",
                len(videos),
                changed_type,
                changed_live,
            )


def _api_key() -> str:
    from app.config import settings

    if not settings.YOUTUBE_DATA_API_KEY:
        raise SystemExit(
            "YOUTUBE_DATA_API_KEY is not configured; cannot re-fetch live "
            "status. Stored live_status is untrustworthy for pre-fix rows."
        )
    return settings.YOUTUBE_DATA_API_KEY


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without writing to the database.",
    )
    args = parser.parse_args()
    asyncio.run(reclassify(dry_run=args.dry_run))
