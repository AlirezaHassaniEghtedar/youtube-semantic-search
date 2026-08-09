import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session_factory
from app.models import Channel, ChannelStatus, Segment, Video, VideoStatus, utcnow
from app.services.captions import fetch_captions
from app.services.downloader import cleanup_audio_file, download_audio, list_channel_videos
from app.services.embedder import EmbedderService, serialize_embedding
from app.services.transcriber import transcribe

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


# NOTE: yt-dlp's extract_flat mode does not return upload_date for most
# channel entries, so published_at is usually None here. We cannot reliably
# filter by exact date in that case. Instead, "recency" is approximated by
# limiting how many entries we fetch via WINDOW_MAX_ITEMS/playlistend in
# list_channel_videos (entries come back newest-first). If published_at IS
# available, we still filter against the window as a secondary check.
def _video_in_window(
    published_at: datetime | None,
    window_start: datetime | None,
    window_end: datetime | None,
) -> bool:
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
    loop = asyncio.get_event_loop()

    async with async_session_factory() as session:
        channel = await session.get(Channel, channel_id)
        if channel is None:
            return

        channel.status = ChannelStatus.FETCHING_LIST
        await session.commit()

        max_items = WINDOW_MAX_ITEMS.get(time_window)
        try:
            videos_data = await loop.run_in_executor(
                _executor, list_channel_videos, channel.url, max_items
            )
        except Exception as exc:
            logger.exception("Failed to list channel videos: %s", exc)
            channel.status = ChannelStatus.ERROR
            await session.commit()
            return

        if videos_data:
            channel.name = videos_data[0].get("channel_name") or channel.name

        window_start, window_end = compute_time_window(
            time_window, start_date, end_date, custom_hours
        )

        if time_window != "all" and any(
            v.get("published_at") is None for v in videos_data
        ):
            logger.debug(
                "upload_date unavailable from flat extraction for channel %s; "
                "recency approximated via playlistend cap (max_items=%s)",
                channel.url,
                max_items,
            )

        filtered = [
            v
            for v in videos_data
            if _video_in_window(v.get("published_at"), window_start, window_end)
        ]

        existing_result = await session.execute(
            select(Video).where(Video.channel_id == channel_id)
        )
        existing_videos = {
            v.youtube_video_id: v for v in existing_result.scalars().all()
        }

        for vd in filtered:
            vid_id = vd["youtube_video_id"]
            if vid_id in existing_videos:
                existing = existing_videos[vid_id]
                if existing.status == VideoStatus.DONE:
                    continue
                existing.title = vd.get("title") or existing.title
                existing.published_at = vd.get("published_at")
                existing.duration_seconds = vd.get("duration_seconds")
                if existing.status == VideoStatus.ERROR:
                    existing.status = VideoStatus.PENDING
                    existing.error_message = None
            else:
                video = Video(
                    channel_id=channel_id,
                    youtube_video_id=vid_id,
                    title=vd.get("title") or "Untitled",
                    published_at=vd.get("published_at"),
                    duration_seconds=vd.get("duration_seconds"),
                    status=VideoStatus.PENDING,
                )
                session.add(video)

        channel.status = ChannelStatus.PROCESSING
        await session.commit()

    await _process_pending_videos(channel_id, whisper_model, embedder)

    async with async_session_factory() as session:
        channel = await session.get(Channel, channel_id)
        if channel is None:
            return

        pending_result = await session.execute(
            select(Video).where(
                Video.channel_id == channel_id,
                Video.status.notin_([VideoStatus.DONE, VideoStatus.ERROR]),
            )
        )
        pending = pending_result.scalars().all()

        if pending:
            channel.status = ChannelStatus.PROCESSING
        else:
            channel.status = ChannelStatus.DONE
            channel.last_synced_at = utcnow()

        await session.commit()


async def _process_pending_videos(
    channel_id: UUID,
    whisper_model,
    embedder: EmbedderService,
) -> None:
    loop = asyncio.get_event_loop()

    async with async_session_factory() as session:
        result = await session.execute(
            select(Video).where(
                Video.channel_id == channel_id,
                Video.status == VideoStatus.PENDING,
            )
        )
        videos = list(result.scalars().all())

    if not videos:
        return

    download_sem = asyncio.Semaphore(settings.MAX_CONCURRENT_DOWNLOADS)
    transcribe_sem = asyncio.Semaphore(settings.MAX_CONCURRENT_TRANSCRIBE)
    tasks = [
        _process_single_video(
            video.id, whisper_model, embedder, loop, download_sem, transcribe_sem
        )
        for video in videos
    ]
    await asyncio.gather(*tasks, return_exceptions=True)


async def _process_single_video(
    video_id: UUID,
    whisper_model,
    embedder: EmbedderService,
    loop: asyncio.AbstractEventLoop,
    download_sem: asyncio.Semaphore,
    transcribe_sem: asyncio.Semaphore,
) -> None:
    youtube_video_id: str | None = None

    try:
        async with async_session_factory() as session:
            video = await session.get(Video, video_id)
            if video is None or video.status != VideoStatus.PENDING:
                return
            youtube_video_id = video.youtube_video_id
            video.error_message = None
            await session.commit()

        segments_data = None
        if settings.PREFER_CAPTIONS:
            async with async_session_factory() as session:
                video = await session.get(Video, video_id)
                if video is None:
                    return
                video.status = VideoStatus.TRANSCRIBING
                logger.info("Video %s status: transcribing (captions)", youtube_video_id)
                await session.commit()
            segments_data = await loop.run_in_executor(
                _executor, fetch_captions, youtube_video_id
            )

        if segments_data is None:
            async with async_session_factory() as session:
                video = await session.get(Video, video_id)
                if video is None:
                    return
                video.status = VideoStatus.DOWNLOADING
                logger.info("Video %s status: downloading", youtube_video_id)
                await session.commit()

            async with download_sem:
                audio_path = await loop.run_in_executor(
                    _executor, download_audio, youtube_video_id
                )

            async with async_session_factory() as session:
                video = await session.get(Video, video_id)
                if video is None:
                    return
                video.status = VideoStatus.TRANSCRIBING
                logger.info("Video %s status: transcribing (Whisper)", youtube_video_id)
                await session.commit()

            async with transcribe_sem:
                segments_data = await loop.run_in_executor(
                    _executor, transcribe, whisper_model, audio_path
                )

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

        texts = [s["text"] for s in segments_data]
        embeddings = await loop.run_in_executor(
            _executor, embedder.embed_batch, texts
        )

        async with async_session_factory() as session:
            video = await session.get(Video, video_id)
            if video is None:
                return

            delete_result = await session.execute(
                select(Segment).where(Segment.video_id == video_id)
            )
            for old_seg in delete_result.scalars().all():
                await session.delete(old_seg)

            for i, seg_data in enumerate(segments_data):
                segment = Segment(
                    video_id=video_id,
                    start_time=seg_data["start"],
                    end_time=seg_data["end"],
                    text=seg_data["text"],
                    embedding=serialize_embedding(embeddings[i]),
                )
                session.add(segment)

            video.status = VideoStatus.DONE
            logger.info("Video %s status: done", youtube_video_id)
            video.error_message = None
            await session.commit()

    except Exception as exc:
        logger.exception("Pipeline failed for video %s: %s", video_id, exc)
        if youtube_video_id:
            for ext in (".m4a", ".mp3", ".webm", ".opus"):
                cleanup_audio_file(settings.download_path / f"{youtube_video_id}{ext}")
        async with async_session_factory() as session:
            video = await session.get(Video, video_id)
            if video:
                video.status = VideoStatus.ERROR
                logger.info("Video %s status: error", youtube_video_id)
                video.error_message = str(exc)
                await session.commit()
