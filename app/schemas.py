from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class ChannelCreate(BaseModel):
    url: str
    time_window: str = Field(
        default="7d", pattern=r"^(24h|7d|30d|custom|custom_hours|all)$"
    )
    start_date: datetime | None = None
    end_date: datetime | None = None
    custom_hours: int | None = Field(default=None, ge=1, le=8760)


class ChannelSummary(BaseModel):
    id: UUID
    url: str
    name: str
    status: str
    last_synced_at: datetime | None
    created_at: datetime
    total_videos: int = 0
    done_videos: int = 0

    model_config = {"from_attributes": True}


class ChannelDetail(ChannelSummary):
    pending_videos: int = 0
    error_videos: int = 0


class SyncJobSummary(BaseModel):
    id: UUID
    time_window: str
    requested_max_items: int | None
    status: str
    new_videos_found: int
    error_message: str | None
    created_at: datetime
    finished_at: datetime | None

    model_config = {"from_attributes": True}


class VideoSummary(BaseModel):
    id: UUID
    channel_id: UUID
    youtube_video_id: str
    title: str
    published_at: datetime | None
    duration_seconds: int | None
    live_status: str | None
    scheduled_start_at: datetime | None
    video_type: str | None
    status: str
    error_message: str | None

    model_config = {"from_attributes": True}


class VideoDetail(VideoSummary):
    channel_name: str = ""
    segment_count: int = 0


class TranscriptSegment(BaseModel):
    segment_id: UUID
    start_time: float
    end_time: float
    text: str
    youtube_link: str


class SearchRequest(BaseModel):
    query: str
    limit: int = Field(default=20, ge=1, le=100)
    channel_id: UUID | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None


class SearchResult(BaseModel):
    segment_id: UUID
    video_id: UUID
    youtube_video_id: str
    video_title: str
    channel_name: str
    start_time: float
    end_time: float
    text: str
    similarity: float
    youtube_link: str


class HealthResponse(BaseModel):
    status: str = "ok"
