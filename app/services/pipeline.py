import asyncio
import logging
import random
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select

from app.config import settings
from app.database import async_session_factory
from app.models import (
    Channel,
    ChannelStatus,
    Segment,
    SyncJob,
    SyncJobStatus,
    Video,
    VideoStatus,
    utcnow,
)
from app.services.captions import fetch_captions
from app.services.downloader import cleanup_audio_file, download_audio, list_channel_videos
from app.services.embedder import EmbedderService, serialize_embedding
from app.services.retry import RateLimitError
from app.services.transcriber import transcribe
from app.services.text_processor import chunk

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(
    max_workers=settings.MAX_CONCURRENT_DOWNLOADS + settings.MAX_CONCURRENT_TRANSCRIBE
)

WINDOW_MAX_ITEMS: dict[str, int | None] = {
    "24h": 50,
    "7d": 150,
    "30d": 400,
    "custom": 1000,
    "custom_hours": 300,
    "all": None,
}

# The event cannot pre-empt a yt-dlp or Whisper call already running in a
# worker thread. It does prevent every subsequent stage from starting.
_stop_events: dict[UUID, asyncio.Event] = {}


def _new_stop_event(channel_id: UUID) -> asyncio.Event:
    event = asyncio.Event()
    _stop_events[channel_id] = event
    return event


def request_stop(channel_id: UUID) -> bool:
    """Request cooperative cancellation for a currently running channel sync."""
    event = _stop_events.get(channel_id)
    if event is None:
        return False
    event.set()
    return True


def clear_stop(channel_id: UUID) -> None:
    """Clear a stale stop request before a new run starts."""
    _stop_events.pop(channel_id, None)


def _clear_stop_event(channel_id: UUID) -> None:
    _stop_events.pop(channel_id, None)


@dataclass
class ProcessingState:
    blocked_until: datetime | None = None
    rate_limit_error: str | None = None
    captions_blocked_until: datetime | None = None

    def is_blocked(self) -> bool:
        return self.blocked_until is not None and utcnow() < self.blocked_until

    def trip(self, message: str) -> None:
        self.blocked_until = utcnow() + timedelta(
            minutes=settings.BOT_CHECK_COOLDOWN_MINUTES
        )
        self.rate_limit_error = message

    def captions_blocked(self) -> bool:
        return (
            self.captions_blocked_until is not None
            and utcnow() < self.captions_blocked_until
        )

    def trip_captions(self, message: str) -> None:
        self.captions_blocked_until = utcnow() + timedelta(
            minutes=settings.CAPTIONS_SKIP_MINUTES
        )
        self.rate_limit_error = message


def compute_time_window(
    time_window: str,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    custom_hours: int | None = None,
) -> tuple[datetime | None, datetime | None]:
    now = utcnow()
    if time_window == "all":
        return None, None
    if time_window == "24h":
        return now - timedelta(hours=24), now
    if time_window == "7d":
        return now - timedelta(days=7), now
    if time_window == "30d":
        return now - timedelta(days=30), now
    if time_window == "custom_hours":
        return now - timedelta(hours=custom_hours or 24), now
    if time_window == "custom":
        start = start_date
        end = end_date or now
        if start and start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end and end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        return start, end
    return now - timedelta(days=7), now


def resolve_fetch_range(
    channel: Channel, requested_max_items: int | None
) -> tuple[int | None, int | None, bool]:
    """Return the still-uncovered newest-first yt-dlp playlist slice.

    Coverage is positional because flat yt-dlp entries often omit upload dates.
    New uploads may shift positions between runs; video-id deduplication makes
    such overlap safe and prevents repeat downloads/transcriptions.
    """
    if channel.synced_all:
        return None, None, True

    already = channel.last_synced_item_count or 0
    if requested_max_items is None:
        return already + 1, None, False
    if requested_max_items <= already:
        return None, None, True
    return already + 1, requested_max_items, False


def _video_in_window(
    published_at: datetime | None,
    window_start: datetime | None,
    window_end: datetime | None,
) -> bool:
    # extract_flat normally has no upload_date. In that case the playlist cap
    # is our recency approximation; a present date remains a useful secondary
    # filter.
    if window_start is None and window_end is None:
        return True
    if published_at is None:
        return True
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    if window_start and published_at < window_start:
        return False
    if window_end and published_at > window_end:
        return False
    return True


async def run_channel_pipeline(
    channel_id: UUID,
    time_window: str,
    start_date: datetime | None,
    end_date: datetime | None,
    whisper_model,
    embedder: EmbedderService,
    custom_hours: int | None = None,
) -> None:
    loop = asyncio.get_running_loop()
    # A new event ensures an old stop request cannot affect a later re-sync.
    stop_event = _new_stop_event(channel_id)
    max_items = WINDOW_MAX_ITEMS.get(time_window)
    sync_job_id: UUID | None = None

    try:
        async with async_session_factory() as session:
            channel = await session.get(Channel, channel_id)
            if channel is None:
                return

            sync_job = SyncJob(
                channel_id=channel_id,
                time_window=time_window,
                requested_max_items=max_items,
                status=SyncJobStatus.RUNNING,
            )
            session.add(sync_job)
            channel.status = ChannelStatus.FETCHING_LIST
            await session.commit()
            await session.refresh(channel)
            await session.refresh(sync_job)
            sync_job_id = sync_job.id

            if stop_event.is_set():
                channel.status = ChannelStatus.STOPPED
                sync_job.status = SyncJobStatus.STOPPED
                sync_job.finished_at = utcnow()
                await session.commit()
                return

            playliststart, playlistend, skip_fetch = resolve_fetch_range(
                channel, max_items
            )
            if skip_fetch:
                logger.info(
                    "Channel %s: requested range already covered; skipping YouTube fetch",
                    channel_id,
                )
                videos_data: list[dict] = []
                sync_job.status = SyncJobStatus.SKIPPED_ALREADY_COVERED
                sync_job.finished_at = utcnow()
            else:
                try:
                    videos_data = await loop.run_in_executor(
                        _executor,
                        list_channel_videos,
                        channel.url,
                        playlistend,
                        playliststart,
                    )
                except RateLimitError as exc:
                    message = "YouTube rate-limited this IP — wait before retrying"
                    logger.error("%s: %s", message, exc)
                    channel.status = ChannelStatus.ERROR
                    sync_job.status = SyncJobStatus.ERROR
                    sync_job.error_message = message
                    sync_job.finished_at = utcnow()
                    await session.commit()
                    return
                except Exception as exc:
                    logger.exception("Failed to list channel videos: %s", exc)
                    channel.status = ChannelStatus.ERROR
                    sync_job.status = SyncJobStatus.ERROR
                    sync_job.error_message = str(exc)
                    sync_job.finished_at = utcnow()
                    await session.commit()
                    return

                # Listing itself is a blocking worker-thread call, so a stop
                # can arrive while it is in flight. Do not create any videos
                # or start processing after that call completes.
                if stop_event.is_set():
                    channel.status = ChannelStatus.STOPPED
                    sync_job.status = SyncJobStatus.STOPPED
                    sync_job.finished_at = utcnow()
                    await session.commit()
                    return

                if max_items is None:
                    channel.synced_all = True
                else:
                    channel.last_synced_item_count = max(
                        channel.last_synced_item_count or 0, max_items
                    )

            if videos_data:
                channel.name = videos_data[0].get("channel_name") or channel.name

            window_start, window_end = compute_time_window(
                time_window, start_date, end_date, custom_hours
            )
            if time_window != "all" and any(
                video.get("published_at") is None for video in videos_data
            ):
                logger.debug(
                    "Flat yt-dlp entries have no upload date for %s; using playlist slice for recency",
                    channel.url,
                )
            filtered = [
                video
                for video in videos_data
                if _video_in_window(video.get("published_at"), window_start, window_end)
            ]

            existing_result = await session.execute(
                select(Video).where(Video.channel_id == channel_id)
            )
            existing = {video.youtube_video_id: video for video in existing_result.scalars()}
            new_count = 0
            for video_data in filtered:
                youtube_video_id = video_data["youtube_video_id"]
                if youtube_video_id in existing:
                    video = existing[youtube_video_id]
                    if video.status == VideoStatus.DONE:
                        continue
                    video.title = video_data.get("title") or video.title
                    video.published_at = video_data.get("published_at")
                    video.duration_seconds = video_data.get("duration_seconds")
                    if video.status == VideoStatus.ERROR:
                        video.status = VideoStatus.PENDING
                        video.error_message = None
                    continue

                session.add(
                    Video(
                        channel_id=channel_id,
                        youtube_video_id=youtube_video_id,
                        title=video_data.get("title") or "Untitled",
                        published_at=video_data.get("published_at"),
                        duration_seconds=video_data.get("duration_seconds"),
                        status=VideoStatus.PENDING,
                    )
                )
                new_count += 1

            sync_job.new_videos_found = new_count
            channel.status = ChannelStatus.PROCESSING
            await session.commit()

        state = await _process_pending_videos(
            channel_id, whisper_model, embedder, stop_event
        )

        async with async_session_factory() as session:
            channel = await session.get(Channel, channel_id)
            job = await session.get(SyncJob, sync_job_id) if sync_job_id else None
            if channel is None:
                return
            if stop_event.is_set():
                channel.status = ChannelStatus.STOPPED
                if job:
                    job.status = SyncJobStatus.STOPPED
                    job.finished_at = utcnow()
            elif state.rate_limit_error:
                message = "YouTube rate-limited this IP — wait before retrying"
                logger.error("%s: %s", message, state.rate_limit_error)
                channel.status = ChannelStatus.ERROR
                if job:
                    job.status = SyncJobStatus.ERROR
                    job.error_message = message
                    job.finished_at = utcnow()
            else:
                pending = await session.scalar(
                    select(Video.id)
                    .where(
                        Video.channel_id == channel_id,
                        Video.status.notin_([VideoStatus.DONE, VideoStatus.ERROR]),
                    )
                    .limit(1)
                )
                channel.status = ChannelStatus.PROCESSING if pending else ChannelStatus.DONE
                if not pending:
                    channel.last_synced_at = utcnow()
                if job and job.status != SyncJobStatus.SKIPPED_ALREADY_COVERED:
                    job.status = SyncJobStatus.DONE
                    job.finished_at = utcnow()
            await session.commit()
    finally:
        _clear_stop_event(channel_id)


async def _restore_pending(video_id: UUID) -> None:
    async with async_session_factory() as session:
        video = await session.get(Video, video_id)
        if video and video.status != VideoStatus.DONE:
            video.status = VideoStatus.PENDING
            await session.commit()


async def _process_pending_videos(
    channel_id: UUID,
    whisper_model,
    embedder: EmbedderService,
    stop_event: asyncio.Event,
) -> ProcessingState:
    loop = asyncio.get_running_loop()
    state = ProcessingState()
    async with async_session_factory() as session:
        result = await session.execute(
            select(Video).where(
                Video.channel_id == channel_id, Video.status == VideoStatus.PENDING
            )
        )
        videos = list(result.scalars())

    captions_sem = asyncio.Semaphore(settings.MAX_CONCURRENT_CAPTIONS)
    download_sem = asyncio.Semaphore(settings.MAX_CONCURRENT_DOWNLOADS)
    transcribe_sem = asyncio.Semaphore(settings.MAX_CONCURRENT_TRANSCRIBE)
    tasks = [
        _process_single_video(
            video.id,
            channel_id,
            whisper_model,
            embedder,
            loop,
            captions_sem,
            download_sem,
            transcribe_sem,
            state,
            stop_event,
        )
        for video in videos
    ]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    return state


async def _process_single_video(
    video_id: UUID,
    channel_id: UUID,
    whisper_model,
    embedder: EmbedderService,
    loop: asyncio.AbstractEventLoop,
    captions_sem: asyncio.Semaphore,
    download_sem: asyncio.Semaphore,
    transcribe_sem: asyncio.Semaphore,
    state: ProcessingState,
    stop_event: asyncio.Event,
) -> None:
    youtube_video_id: str | None = None

    async def should_stop() -> bool:
        if stop_event.is_set() or state.is_blocked():
            await _restore_pending(video_id)
            return True
        return False

    try:
        if await should_stop():
            return
        async with async_session_factory() as session:
            video = await session.get(Video, video_id)
            if video is None or video.status != VideoStatus.PENDING:
                return
            youtube_video_id = video.youtube_video_id
            video.error_message = None
            await session.commit()

        segments_data = None
        if settings.PREFER_CAPTIONS and not state.captions_blocked():
            if await should_stop():
                return
            async with async_session_factory() as session:
                video = await session.get(Video, video_id)
                if video is None:
                    return
                video.status = VideoStatus.TRANSCRIBING
                logger.info("Video %s status: transcribing (captions)", youtube_video_id)
                await session.commit()

            await asyncio.sleep(
                random.uniform(
                    settings.CAPTIONS_JITTER_MIN_SECONDS,
                    settings.CAPTIONS_JITTER_MAX_SECONDS,
                )
            )
            if await should_stop():
                return
            try:
                async with captions_sem:
                    if await should_stop():
                        return
                    segments_data = await loop.run_in_executor(
                        _executor, fetch_captions, youtube_video_id
                    )
            except RateLimitError as exc:
                logger.warning(
                    "Possible caption block on video %s: %s. Skipping "
                    "captions for %d minutes and using Whisper instead.",
                    youtube_video_id,
                    exc,
                    settings.CAPTIONS_SKIP_MINUTES,
                )
                state.trip_captions(str(exc))
                segments_data = None

        if segments_data is None:
            if await should_stop():
                return
            async with async_session_factory() as session:
                video = await session.get(Video, video_id)
                if video is None:
                    return
                video.status = VideoStatus.DOWNLOADING
                logger.info("Video %s status: downloading", youtube_video_id)
                await session.commit()

            # Jitter prevents semaphore releases from creating request bursts.
            await asyncio.sleep(
                random.uniform(
                    settings.DOWNLOAD_JITTER_MIN_SECONDS,
                    settings.DOWNLOAD_JITTER_MAX_SECONDS,
                )
            )
            if await should_stop():
                return
            try:
                async with download_sem:
                    if await should_stop():
                        return
                    audio_path = await loop.run_in_executor(
                        _executor, download_audio, youtube_video_id
                    )
            except RateLimitError as exc:
                logger.error("YouTube rate limit on video %s: %s", youtube_video_id, exc)
                state.trip(str(exc))
                await _restore_pending(video_id)
                return

            if await should_stop():
                return
            async with async_session_factory() as session:
                video = await session.get(Video, video_id)
                if video is None:
                    return
                video.status = VideoStatus.TRANSCRIBING
                logger.info("Video %s status: transcribing (Whisper)", youtube_video_id)
                await session.commit()

            if await should_stop():
                return
            async with transcribe_sem:
                if await should_stop():
                    return
                segments_data = await loop.run_in_executor(
                    _executor, transcribe, whisper_model, audio_path
                )

        if await should_stop():
            return
        if not segments_data:
            async with async_session_factory() as session:
                video = await session.get(Video, video_id)
                if video:
                    video.status = VideoStatus.DONE
                    logger.info("Video %s status: done (no segments)", youtube_video_id)
                    await session.commit()
            return

        segments_data = chunk(segments_data, chunk_size=500, chunk_overlap=100)
        if not segments_data:
            async with async_session_factory() as session:
                video = await session.get(Video, video_id)
                if video:
                    video.status = VideoStatus.DONE
                    await session.commit()
            return

        async with async_session_factory() as session:
            video = await session.get(Video, video_id)
            if video is None:
                return
            video.status = VideoStatus.EMBEDDING
            logger.info("Video %s status: embedding", youtube_video_id)
            await session.commit()

        if await should_stop():
            return
        texts = [segment["text"] for segment in segments_data]
        embeddings = await loop.run_in_executor(_executor, embedder.embed_batch, texts)
        if await should_stop():
            return

        async with async_session_factory() as session:
            video = await session.get(Video, video_id)
            if video is None:
                return
            old_segments = await session.execute(
                select(Segment).where(Segment.video_id == video_id)
            )
            for old_segment in old_segments.scalars():
                await session.delete(old_segment)
            for index, segment_data in enumerate(segments_data):
                session.add(
                    Segment(
                        video_id=video_id,
                        start_time=segment_data["start"],
                        end_time=segment_data["end"],
                        text=segment_data["text"],
                        embedding=serialize_embedding(embeddings[index]),
                    )
                )
            video.status = VideoStatus.DONE
            video.error_message = None
            logger.info("Video %s status: done", youtube_video_id)
            await session.commit()
    except Exception as exc:
        logger.exception("Pipeline failed for video %s: %s", video_id, exc)
        if youtube_video_id:
            for extension in (".m4a", ".mp3", ".webm", ".opus"):
                cleanup_audio_file(settings.download_path / f"{youtube_video_id}{extension}")
        async with async_session_factory() as session:
            video = await session.get(Video, video_id)
            if video:
                video.status = VideoStatus.ERROR
                video.error_message = str(exc)
                logger.info("Video %s status: error", youtube_video_id)
                await session.commit()
