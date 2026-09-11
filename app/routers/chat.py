import json
import logging
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import ChatConversation, ChatMessage
from app.schemas import ChatRequest, ChatResponse
from app.services.chat import answer_question, generate_conversation_title

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatRequestWithConversation(BaseModel):
    question: str
    conversation_id: UUID
    channel_id: str | None = None


@router.post("", response_model=ChatResponse)
async def chat(
    payload: ChatRequestWithConversation,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ChatResponse:
    """
    Chat endpoint: ask a question within a conversation context.
    
    Saves messages to database, generates answer using RAG, and auto-titles
    the conversation on first exchange.
    """
    embedder = request.app.state.embedder
    
    # Verify conversation exists
    conv_stmt = select(ChatConversation).where(
        ChatConversation.id == payload.conversation_id
    )
    conv_result = await db.execute(conv_stmt)
    conversation = conv_result.scalars().first()
    
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    
    # Save user message
    user_msg = ChatMessage(
        conversation_id=payload.conversation_id,
        role="user",
        content=payload.question,
        sources=None,
    )
    db.add(user_msg)
    await db.flush()
    
    # Get answer from RAG
    response = await answer_question(
        question=payload.question,
        db=db,
        embedder=embedder,
        channel_id=payload.channel_id,
        top_k=8,
    )
    
    # Serialize sources to JSON
    sources_json = None
    if response.sources:
        sources_json = json.dumps([s.model_dump() for s in response.sources])
    
    # Save assistant message
    assistant_msg = ChatMessage(
        conversation_id=payload.conversation_id,
        role="assistant",
        content=response.answer,
        sources=sources_json,
    )
    db.add(assistant_msg)
    
    # Auto-generate title if not set
    if not conversation.title:
        conversation.title = await generate_conversation_title(payload.question)
    
    # Update conversation timestamp
    conversation.updated_at = datetime.now(timezone.utc)
    await db.commit()
    
    response.title = conversation.title
    return response
