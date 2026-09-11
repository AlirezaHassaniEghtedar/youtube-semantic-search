import logging
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas import ChatRequest, ChatResponse
from app.services.chat import answer_question

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.post("", response_model=ChatResponse)
async def chat(
    payload: ChatRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ChatResponse:
    """
    Chat endpoint: ask a natural-language question about video transcripts.
    
    Returns:
    - answer: prose answer from Gemini
    - sources: list of relevant video segments that support the answer
               (using the same SearchResult schema as /api/search)
    """
    embedder = request.app.state.embedder
    
    return await answer_question(
        question=payload.question,
        db=db,
        embedder=embedder,
        channel_id=payload.channel_id,
        top_k=8,
    )
