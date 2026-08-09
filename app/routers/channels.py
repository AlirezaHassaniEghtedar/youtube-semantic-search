import logging
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Channel, ChannelStatus, Video, VideoStatus
from app.schemas import ChannelCreate, ChannelDetail, ChannelSummary
from app.config import settings
from app.services.downloader import cleanup_audio_file
from app.services.pipeline import run_channel_pipeline

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/channels", tags=["channels"])

TERMINAL_CHANNEL_STATUSES = {ChannelStatus.DONE, ChannelStatus.ERROR}
TERMINAL_VIDEO_STATUSES = {VideoStatus.DONE, VideoStatus.ERROR}


async def _channel_counts(session: AsyncSession, channel_id: UUID) -> dict[str, int]:
    total = await session.scalar(
        select(func.count()).select_from(Video).where(Video.channel_id == channel_id)
    )
    done = await session.scalar(
        select(func.count())
        .select_from(Video)
        .where(Video.channel_id == channel_id, Video.status == VideoStatus.DONE)
    )
    pending = await session.scalar(
        select(func.count())
        .select_from(Video)
        .where(
            Video.channel_id == channel_id,
            Video.status.notin_(list(TERMINAL_VIDEO_STATUSES)),
        )
    )
    error = await session.scalar(
        select(func.count())
        .select_from(Video)
        .where(Video.channel_id == channel_id, Video.status == VideoStatus.ERROR)
    )
    return {
        "total_videos": total or 0,
        "done_videos": done or 0,
        "pending_videos": pending or 0,
        "error_videos": error or 0,
    }


def _to_summary(channel: Channel, counts: dict[str, int]) -> ChannelSummary:
    return ChannelSummary(
        id=channel.id,
        url=channel.url,
        name=channel.name,
        status=channel.status.value,
        last_synced_at=channel.last_synced_at,
        created_at=channel.created_at,
        total_videos=counts["total_videos"],
        done_videos=counts["done_videos"],
    )


@router.post("", response_model=ChannelSummary, status_code=201)
async def create_or_update_channel(
    payload: ChannelCreate,
    background_tasks: BackgroundTasks,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    if payload.time_window == "custom" and not payload.start_date:
        raise HTTPException(
            status_code=400,
            detail="start_date is required for custom time window",
        )

    result = await db.execute(
        select(Channel).where(Channel.url == payload.url)
    )
    channel = result.scalar_one_or_none()

    if channel is None:
        channel = Channel(url=payload.url, name="", status=ChannelStatus.PENDING)
        db.add(channel)
        await db.flush()
    else:
        channel.status = ChannelStatus.PENDING

    await db.commit()
    await db.refresh(channel)

    whisper_model = request.app.state.whisper
    embedder = request.app.state.embedder

    background_tasks.add_task(
        run_channel_pipeline,
        channel.id,
        payload.time_window,
        payload.start_date,
        payload.end_date,
        whisper_model,
        embedder,
    )

    counts = await _channel_counts(db, channel.id)
    return _to_summary(channel, counts)


@router.get("", response_model=list[ChannelSummary])
async def list_channels(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Channel).order_by(Channel.created_at.desc()))
    channels = result.scalars().all()
    summaries = []
    for channel in channels:
        counts = await _channel_counts(db, channel.id)
        summaries.append(_to_summary(channel, counts))
    return summaries


@router.get("/{channel_id}", response_model=ChannelDetail)
async def get_channel(channel_id: UUID, db: AsyncSession = Depends(get_db)):
    channel = await db.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")

    counts = await _channel_counts(db, channel_id)
    return ChannelDetail(
        id=channel.id,
        url=channel.url,
        name=channel.name,
        status=channel.status.value,
        last_synced_at=channel.last_synced_at,
        created_at=channel.created_at,
        total_videos=counts["total_videos"],
        done_videos=counts["done_videos"],
        pending_videos=counts["pending_videos"],
        error_videos=counts["error_videos"],
    )


@router.delete("/{channel_id}", status_code=204)
async def delete_channel(channel_id: UUID, db: AsyncSession = Depends(get_db)):
    channel = await db.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")

    result = await db.execute(
        select(Video).where(Video.channel_id == channel_id)
    )
    videos = result.scalars().all()
    for video in videos:
        cleanup_audio_file(
            settings.download_path / f"{video.youtube_video_id}.m4a"
        )

    await db.delete(channel)
    await db.commit()


@router.get("/{channel_id}/videos", response_model=list)
async def list_channel_videos(channel_id: UUID, db: AsyncSession = Depends(get_db)):
    channel = await db.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")

    result = await db.execute(
        select(Video)
        .where(Video.channel_id == channel_id)
        .order_by(Video.published_at.desc().nullslast(), Video.created_at.desc())
    )
    videos = result.scalars().all()
    return [
        {
            "id": str(v.id),
            "channel_id": str(v.channel_id),
            "youtube_video_id": v.youtube_video_id,
            "title": v.title,
            "published_at": v.published_at.isoformat() if v.published_at else None,
            "duration_seconds": v.duration_seconds,
            "status": v.status.value,
            "error_message": v.error_message,
        }
        for v in videos
    ]
