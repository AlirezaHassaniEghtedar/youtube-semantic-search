import json
import logging
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import ChatConversation, ChatMessage, Channel
from app.schemas import (
    ChatConversationSummary,
    ChatMessageData,
    CreateConversationRequest,
    RenameConversationRequest,
    SearchResult,
)
from app.services.chat import answer_question, generate_conversation_title
from app.services.embedder import EmbedderService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


@router.post("", response_model=ChatConversationSummary)
async def create_conversation(
    payload: CreateConversationRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create a new empty conversation."""
    conv = ChatConversation(
        title=None,
        channel_id=payload.channel_id,
    )
    db.add(conv)
    await db.commit()
    await db.refresh(conv)
    
    return ChatConversationSummary(
        id=conv.id,
        title=conv.title,
        preview="",
        created_at=conv.created_at,
        updated_at=conv.updated_at,
    )


@router.get("", response_model=list[ChatConversationSummary])
async def list_conversations(
    db: AsyncSession = Depends(get_db),
):
    """List all conversations, most recently updated first."""
    stmt = select(ChatConversation).order_by(desc(ChatConversation.updated_at))
    result = await db.execute(stmt)
    conversations = result.scalars().all()
    
    summaries = []
    for conv in conversations:
        preview = ""
        if conv.messages:
            last_msg = conv.messages[-1]
            preview_text = last_msg.content[:60]
            preview = preview_text + "..." if len(last_msg.content) > 60 else preview_text
        
        summaries.append(
            ChatConversationSummary(
                id=conv.id,
                title=conv.title,
                preview=preview,
                created_at=conv.created_at,
                updated_at=conv.updated_at,
            )
        )
    
    return summaries


@router.get("/{conversation_id}/messages", response_model=list[ChatMessageData])
async def get_conversation_messages(
    conversation_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get all messages in a conversation."""
    stmt = select(ChatMessage).where(
        ChatMessage.conversation_id == conversation_id
    ).order_by(ChatMessage.created_at)
    
    result = await db.execute(stmt)
    messages = result.scalars().all()
    
    if not messages:
        # Check if conversation exists
        conv_stmt = select(ChatConversation).where(
            ChatConversation.id == conversation_id
        )
        conv_result = await db.execute(conv_stmt)
        if not conv_result.scalars().first():
            raise HTTPException(status_code=404, detail="Conversation not found")
    
    data = []
    for msg in messages:
        sources = None
        if msg.sources:
            try:
                sources_list = json.loads(msg.sources)
                sources = [SearchResult(**s) for s in sources_list]
            except Exception as e:
                logger.error(f"Failed to parse sources JSON: {e}")
        
        data.append(
            ChatMessageData(
                id=msg.id,
                conversation_id=msg.conversation_id,
                role=msg.role,
                content=msg.content,
                sources=sources,
                created_at=msg.created_at,
            )
        )
    
    return data


@router.patch("/{conversation_id}", response_model=ChatConversationSummary)
async def rename_conversation(
    conversation_id: UUID,
    payload: RenameConversationRequest,
    db: AsyncSession = Depends(get_db),
):
    """Rename a conversation."""
    stmt = select(ChatConversation).where(
        ChatConversation.id == conversation_id
    )
    result = await db.execute(stmt)
    conv = result.scalars().first()
    
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    
    conv.title = payload.title
    conv.updated_at = datetime.now(timezone.utc)
    await db.commit()
    
    preview = ""
    if conv.messages:
        last_msg = conv.messages[-1]
        preview_text = last_msg.content[:60]
        preview = preview_text + "..." if len(last_msg.content) > 60 else preview_text
    
    return ChatConversationSummary(
        id=conv.id,
        title=conv.title,
        preview=preview,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
    )


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """Delete a conversation and all its messages."""
    stmt = select(ChatConversation).where(
        ChatConversation.id == conversation_id
    )
    result = await db.execute(stmt)
    conv = result.scalars().first()
    
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    
    await db.delete(conv)
    await db.commit()
