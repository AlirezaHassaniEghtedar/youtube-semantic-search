import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import Channel, Segment, Video, VideoStatus
from app.schemas import SearchRequest, SearchResult
from app.services.embedder import EmbedderService, deserialize_embedding

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/search", tags=["search"])

_executor = ThreadPoolExecutor(max_workers=2)


def _run_search(
    embedder: EmbedderService,
    query: str,
    rows: list,
    limit: int,
) -> list[SearchResult]:
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

@router.post("", response_model=list[SearchResult])
async def search_segments(
    payload: SearchRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    if not payload.query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty")

    embedder: EmbedderService = request.app.state.embedder
    limit = min(payload.limit, settings.MAX_SEARCH_RESULTS)

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

    if payload.channel_id:
        stmt = stmt.where(Video.channel_id == payload.channel_id)

    if payload.date_from:
        stmt = stmt.where(Video.published_at >= payload.date_from)

    if payload.date_to:
        stmt = stmt.where(Video.published_at <= payload.date_to)

    result = await db.execute(stmt)
    rows = result.all()

    if not rows:
        return []

    loop = asyncio.get_event_loop()
    results = await loop.run_in_executor(
        _executor,
        _run_search,
        embedder,
        payload.query,
        rows,
        limit,
    )
    return results
