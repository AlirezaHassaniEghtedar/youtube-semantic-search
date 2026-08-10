import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    Boolean,
    Enum,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class ChannelStatus(str, enum.Enum):
    PENDING = "pending"
    FETCHING_LIST = "fetching_list"
    PROCESSING = "processing"
    DONE = "done"
    ERROR = "error"
    STOPPED = "stopped"


class SyncJobStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    STOPPED = "stopped"
    SKIPPED_ALREADY_COVERED = "skipped_already_covered"


class VideoStatus(str, enum.Enum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    TRANSCRIBING = "transcribing"
    EMBEDDING = "embedding"
    DONE = "done"
    ERROR = "error"


class Channel(Base):
    __tablename__ = "channels"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    url: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    status: Mapped[ChannelStatus] = mapped_column(
        Enum(ChannelStatus, native_enum=False),
        default=ChannelStatus.PENDING,
        nullable=False,
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_synced_item_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    synced_all: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Resolved once by yt-dlp and then reused for precise recent-video RSS reads.
    youtube_channel_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    videos: Mapped[list["Video"]] = relationship(
        "Video", back_populates="channel", cascade="all, delete-orphan"
    )
    sync_jobs: Mapped[list["SyncJob"]] = relationship(
        "SyncJob", back_populates="channel", cascade="all, delete-orphan"
    )


class SyncJob(Base):
    __tablename__ = "sync_jobs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    channel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"), nullable=False
    )
    time_window: Mapped[str] = mapped_column(String(32), nullable=False)
    requested_max_items: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[SyncJobStatus] = mapped_column(
        Enum(SyncJobStatus, native_enum=False),
        default=SyncJobStatus.PENDING,
        nullable=False,
    )
    new_videos_found: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    channel: Mapped["Channel"] = relationship("Channel", back_populates="sync_jobs")


class Video(Base):
    __tablename__ = "videos"
    __table_args__ = (
        UniqueConstraint("channel_id", "youtube_video_id", name="uq_channel_video"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    channel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"), nullable=False
    )
    youtube_video_id: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[VideoStatus] = mapped_column(
        Enum(VideoStatus, native_enum=False),
        default=VideoStatus.PENDING,
        nullable=False,
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    channel: Mapped["Channel"] = relationship("Channel", back_populates="videos")
    segments: Mapped[list["Segment"]] = relationship(
        "Segment", back_populates="video", cascade="all, delete-orphan"
    )


class Segment(Base):
    __tablename__ = "segments"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    video_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("videos.id", ondelete="CASCADE"), nullable=False
    )
    start_time: Mapped[float] = mapped_column(Float, nullable=False)
    end_time: Mapped[float] = mapped_column(Float, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    video: Mapped["Video"] = relationship("Video", back_populates="segments")
