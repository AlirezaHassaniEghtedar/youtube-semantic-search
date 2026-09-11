import asyncio
import functools
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session_factory, get_db
from app.models import Channel, ChannelStatus, Segment, Video, VideoStatus
from app.schemas import LocalVideoCreate, VideoSummary
from app.services.embedder import EmbedderService, serialize_embedding
from app.services.subtitles import (
    SUPPORTED_SUBTITLE_EXTENSIONS,
    SUPPORTED_VIDEO_EXTENSIONS,
    SubtitleParseError,
    parse_subtitle_file,
    probe_duration_seconds,
    resolve_subtitle_segments,
)
from app.services.text_processor import chunk
from app.services.transcriber import transcribe

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/local-videos", tags=["local-videos"])

_executor = ThreadPoolExecutor(max_workers=2)

# Sentinel channel URL used to bucket every locally-added video under one
# "Local Videos" card in the existing channels UI, so no schema change is
# needed to make Video.channel_id optional and no new list UI is needed.
LOCAL_CHANNEL_URL = "local://videos"
LOCAL_CHANNEL_NAME = "Local Videos"


async def _get_or_create_local_channel(db: AsyncSession) -> Channel:
    result = await db.execute(select(Channel).where(Channel.url == LOCAL_CHANNEL_URL))
    channel = result.scalar_one_or_none()
    if channel is None:
        channel = Channel(
            url=LOCAL_CHANNEL_URL,
            name=LOCAL_CHANNEL_NAME,
            status=ChannelStatus.DONE,
        )
        db.add(channel)
        await db.flush()
    return channel


def _to_summary(video: Video) -> VideoSummary:
    return VideoSummary(
        id=video.id,
        channel_id=video.channel_id,
        youtube_video_id=video.youtube_video_id,
        title=video.title,
        published_at=video.published_at,
        duration_seconds=video.duration_seconds,
        live_status=video.live_status,
        scheduled_start_at=video.scheduled_start_at,
        video_type=video.video_type,
        status=video.status.value,
        error_message=video.error_message,
    )


@router.post("", response_model=VideoSummary, status_code=201)
async def add_local_video(
    payload: LocalVideoCreate,
    background_tasks: BackgroundTasks,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    video_path = Path(payload.video_path).expanduser()

    if not video_path.exists():
        raise HTTPException(status_code=400, detail=f"Video file not found: {video_path}")
    if video_path.suffix.lower() not in SUPPORTED_VIDEO_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported video format '{video_path.suffix}'. "
                f"Supported: {', '.join(sorted(SUPPORTED_VIDEO_EXTENSIONS))}"
            ),
        )

    # Subtitle resolution order (see process_local_video): an explicit path
    # from the request wins outright; otherwise the background task tries
    # an embedded subtitle track, then a same-named sibling file, then
    # Whisper transcription. Only an explicit path is validated here since
    # the other two checks involve ffmpeg/ffprobe calls we don't want to
    # block this request on.
    subtitle_path: Path | None = None
    if payload.subtitle_path:
        subtitle_path = Path(payload.subtitle_path).expanduser()
        if not subtitle_path.exists():
            raise HTTPException(
                status_code=400, detail=f"Subtitle file not found: {subtitle_path}"
            )
        if subtitle_path.suffix.lower() not in SUPPORTED_SUBTITLE_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Unsupported subtitle format '{subtitle_path.suffix}'. "
                    f"Supported: {', '.join(sorted(SUPPORTED_SUBTITLE_EXTENSIONS))}"
                ),
            )

    channel = await _get_or_create_local_channel(db)

    title = payload.title.strip() if payload.title and payload.title.strip() else video_path.stem
    duration_seconds = probe_duration_seconds(video_path)
    resolved_video_path = video_path.resolve()

    video = Video(
        channel_id=channel.id,
        # Synthetic unique id keeps the existing (channel_id, youtube_video_id)
        # uniqueness constraint satisfied without touching that column's schema.
        youtube_video_id=f"local:{uuid.uuid4().hex}",
        title=title,
        duration_seconds=duration_seconds,
        source_type="local",
        local_file_path=str(resolved_video_path),
        status=VideoStatus.PENDING,
    )
    db.add(video)
    await db.commit()
    await db.refresh(video)

    whisper_model = request.app.state.whisper
    embedder: EmbedderService = request.app.state.embedder
    background_tasks.add_task(
        process_local_video,
        video.id,
        str(subtitle_path.resolve()) if subtitle_path else None,
        str(resolved_video_path),
        whisper_model,
        embedder,
    )

    return _to_summary(video)


@router.get("", response_model=list[VideoSummary])
async def list_local_videos(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Channel).where(Channel.url == LOCAL_CHANNEL_URL))
    channel = result.scalar_one_or_none()
    if channel is None:
        return []
    result = await db.execute(
        select(Video)
        .where(Video.channel_id == channel.id)
        .order_by(Video.created_at.desc())
    )
    return [_to_summary(v) for v in result.scalars().all()]


@router.delete("/{video_id}", status_code=204)
async def delete_local_video(video_id: UUID, db: AsyncSession = Depends(get_db)):
    video = await db.get(Video, video_id)
    if video is None or video.source_type != "local":
        raise HTTPException(status_code=404, detail="Local video not found")
    await db.delete(video)
    await db.commit()


@router.get("/{video_id}/media")
async def stream_local_video(video_id: UUID, db: AsyncSession = Depends(get_db)):
    """Serve a local video's file for in-browser playback. Starlette's
    FileResponse handles HTTP Range requests, which is what lets the '#t='
    fragment seek and lets the player scrub without downloading the whole
    file first.
    """
    video = await db.get(Video, video_id)
    if video is None or video.source_type != "local" or not video.local_file_path:
        raise HTTPException(status_code=404, detail="Local video not found")

    path = Path(video.local_file_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Video file is missing on disk")

    return FileResponse(path, filename=path.name)


async def process_local_video(
    video_id: UUID,
    explicit_subtitle_path: str | None,
    video_path: str,
    whisper_model,
    embedder: EmbedderService,
) -> None:
    """Get segment text -> chunk -> embed -> store segments. Runs in the
    background the same way channel video processing does.

    Subtitle resolution order, entirely inside this background task so
    none of it blocks the API response:
      1. explicit_subtitle_path, if the user provided one directly.
      2. An embedded subtitle track inside the video container.
      3. A same-named sibling subtitle file next to the video.
      4. Whisper transcription of the video's audio, via the same model
         instance app.state.whisper the YouTube pipeline uses.
         cleanup=False is critical: the transcriber's default behavior
         deletes the file it was given afterwards, which is correct for
         temp YouTube downloads but would destroy the user's actual local
         video file here.
    """
    async with async_session_factory() as session:
        video = await session.get(Video, video_id)
        if video is None:
            return
        try:
            loop = asyncio.get_running_loop()
            video_path_obj = Path(video_path)

            if explicit_subtitle_path:
                segments_data = await loop.run_in_executor(
                    _executor, parse_subtitle_file, Path(explicit_subtitle_path)
                )
                source = "provided"
            else:
                scratch_dir = settings.download_path / "subtitle_scratch"
                resolve_call = functools.partial(
                    resolve_subtitle_segments, video_path_obj, scratch_dir
                )
                segments_data, source = await loop.run_in_executor(_executor, resolve_call)

                if source == "whisper":
                    video.status = VideoStatus.TRANSCRIBING
                    video.error_message = None
                    logger.info("Local video %s status: transcribing (Whisper)", video_id)
                    await session.commit()

                    transcribe_call = functools.partial(
                        transcribe, whisper_model, video_path_obj, cleanup=False
                    )
                    segments_data = await loop.run_in_executor(_executor, transcribe_call)

            logger.info("Local video %s subtitle source: %s", video_id, source)
            segments_data = chunk(segments_data, chunk_size=500, chunk_overlap=100)

            if not segments_data:
                video.status = VideoStatus.DONE
                await session.commit()
                return

            video.status = VideoStatus.EMBEDDING
            logger.info("Local video %s status: embedding", video_id)
            await session.commit()

            texts = [segment["text"] for segment in segments_data]
            embeddings = await loop.run_in_executor(
                _executor, embedder.embed_batch, texts
            )

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
            logger.info("Local video %s status: done (%d segments)", video_id, len(segments_data))
            await session.commit()
        except SubtitleParseError as exc:
            video.status = VideoStatus.ERROR
            video.error_message = str(exc)
            await session.commit()
        except Exception as exc:
            logger.exception("Failed to process local video %s", video_id)
            video.status = VideoStatus.ERROR
            video.error_message = str(exc)
            await session.commit()