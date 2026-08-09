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

    if with_timestamps:
        return [
            TranscriptSegment(
                start_time=s.start_time,
                end_time=s.end_time,
                text=s.text,
                youtube_link=(
                    f"https://www.youtube.com/watch?v={video.youtube_video_id}"
                    f"&t={int(s.start_time)}s"
                ),
            ).model_dump()
            for s in segments
        ]

    merged = " ".join(s.text for s in segments)
    return {"text": merged}
