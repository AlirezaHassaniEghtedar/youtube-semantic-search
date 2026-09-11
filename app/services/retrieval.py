import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Channel, Segment, Video, VideoStatus
from app.schemas import SearchResult
from app.services.embedder import EmbedderService, deserialize_embedding

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=2)


def _rank_by_similarity(
    embedder: EmbedderService,
    query: str,
    rows: list,
    limit: int,
) -> list[SearchResult]:
    """Rank segments by cosine similarity to query."""
    if not rows:
        return []

    matrix = np.vstack([deserialize_embedding(r.embedding) for r in rows])
    query_vec = embedder.embed_text(query)
    similarities = matrix @ query_vec
    top_indices = np.argsort(-similarities)[:limit]

    results: list[SearchResult] = []
    for idx in top_indices:
        r = rows[idx]
        sim = float(similarities[idx])
        is_local = r.source_type == "local"
        results.append(
            SearchResult(
                segment_id=r.id,
                video_id=r.video_id,
                youtube_video_id=r.youtube_video_id,
                video_title=r.title,
                channel_name=r.channel_name,
                start_time=r.start_time,
                end_time=r.end_time,
                text=r.text,
                similarity=sim,
                source_type=r.source_type,
                youtube_link=(
                    None
                    if is_local
                    else f"https://www.youtube.com/watch?v={r.youtube_video_id}&t={int(r.start_time)}s"
                ),
                media_url=(
                    f"/api/local-videos/{r.video_id}/media#t={r.start_time:.2f}"
                    if is_local
                    else None
                ),
            )
        )
    return results


async def retrieve_segments(
    db: AsyncSession,
    embedder: EmbedderService,
    query: str,
    channel_id: UUID | None = None,
    top_k: int = 8,
) -> list[SearchResult]:
    """
    Retrieve top-K most similar segments for a query.
    
    Used by both search and chat endpoints to get candidate segments
    before ranking/filtering.
    """
    stmt = (
        select(
            Segment.id,
            Segment.video_id,
            Segment.start_time,
            Segment.end_time,
            Segment.text,
            Segment.embedding,
            Video.title,
            Video.youtube_video_id,
            Video.published_at,
            Video.source_type,
            Video.local_file_path,
            Channel.name.label("channel_name"),
        )
        .join(Video, Segment.video_id == Video.id)
        .join(Channel, Video.channel_id == Channel.id)
        .where(Video.status == VideoStatus.DONE)
    )

    if channel_id:
        stmt = stmt.where(Video.channel_id == channel_id)

    result = await db.execute(stmt)
    rows = result.all()

    if not rows:
        return []

    loop = asyncio.get_event_loop()
    results = await loop.run_in_executor(
        _executor,
        _rank_by_similarity,
        embedder,
        query,
        rows,
        top_k,
    )
    return results
