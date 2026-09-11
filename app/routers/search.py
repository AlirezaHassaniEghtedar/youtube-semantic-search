import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.schemas import SearchRequest, SearchResult
from app.services.retrieval import retrieve_segments

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/search", tags=["search"])


@router.post("", response_model=list[SearchResult])
async def search_segments(
    payload: SearchRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    if not payload.query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty")

    embedder = request.app.state.embedder
    limit = min(payload.limit, settings.MAX_SEARCH_RESULTS)

    results = await retrieve_segments(
        db=db,
        embedder=embedder,
        query=payload.query,
        channel_id=payload.channel_id,
        top_k=limit,
    )
    return results
