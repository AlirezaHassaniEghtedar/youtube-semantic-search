from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Channel, Segment, Video
from app.schemas import TranscriptSegment, VideoDetail

router = APIRouter(prefix="/api/videos", tags=["videos"])


@router.get("/{video_id}", response_model=VideoDetail)
async def get_video(video_id: UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Video, Channel.name)
        .join(Channel, Video.channel_id == Channel.id)
        .where(Video.id == video_id)
    )
    row = result.one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Video not found")

    video, channel_name = row
    segment_count = await db.scalar(
        select(func.count()).select_from(Segment).where(Segment.video_id == video_id)
    )

    return VideoDetail(
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
        channel_name=channel_name,
        segment_count=segment_count or 0,
    )


@router.get("/{video_id}/transcript")
async def get_transcript(
    video_id: UUID,
    with_timestamps: bool = Query(default=True),
    db: AsyncSession = Depends(get_db),
):
    video = await db.get(Video, video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")

    result = await db.execute(
        select(Segment)
        .where(Segment.video_id == video_id)
        .order_by(Segment.start_time)
    )
    segments = result.scalars().all()
    is_local = video.source_type == "local"

    if with_timestamps:
        return [
            TranscriptSegment(
                segment_id=s.id,
                start_time=s.start_time,
                end_time=s.end_time,
                text=s.text,
                source_type=video.source_type,
                youtube_link=(
                    None
                    if is_local
                    else f"https://www.youtube.com/watch?v={video.youtube_video_id}&t={int(s.start_time)}s"
                ),
                media_url=(
                    f"/api/local-videos/{video.id}/media#t={s.start_time:.2f}"
                    if is_local
                    else None
                ),
            ).model_dump()
            for s in segments
        ]
    merged = " ".join(s.text for s in segments)
    return {"text": merged}
