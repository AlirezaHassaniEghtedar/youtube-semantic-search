import json
import logging
from datetime import datetime, timezone
from uuid import UUID
from sqlalchemy.orm import selectinload

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import ChatConversation, ChatMessage, Channel
from app.schemas import (
    ChatConversationSummary,
    ChatMessageData,
    CreateConversationRequest,
    RenameConversationRequest,
    SearchResult,
    TruncateConversationRequest,
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
    stmt = (
    select(ChatConversation)
    .options(selectinload(ChatConversation.messages))
    .order_by(desc(ChatConversation.updated_at))
)
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


@router.post("/{conversation_id}/truncate", status_code=status.HTTP_204_NO_CONTENT)
async def truncate_conversation_after_message(
    conversation_id: UUID,
    payload: TruncateConversationRequest,
    db: AsyncSession = Depends(get_db),
):
    """Delete all messages created strictly AFTER the given message.

    Used by the chat UI's "edit message" feature: the edited message itself
    stays, everything after it is discarded, and the client re-sends the
    edited text as a fresh message via POST /api/chat.
    """
    conv_result = await db.execute(
        select(ChatConversation).where(ChatConversation.id == conversation_id)
    )
    conv = conv_result.scalars().first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    anchor_result = await db.execute(
        select(ChatMessage).where(
            ChatMessage.id == payload.after_message_id,
            ChatMessage.conversation_id == conversation_id,
        )
    )
    anchor = anchor_result.scalars().first()
    if not anchor:
        raise HTTPException(status_code=404, detail="Message not found in this conversation")

    # created_at has second resolution in SQLite and several messages can share
    # a timestamp, so "after the anchor" is derived from created_at OR the
    # anchor row itself, never from a strict timestamp comparison alone.
    await db.execute(
        delete(ChatMessage).where(
            ChatMessage.conversation_id == conversation_id,
            or_(
                ChatMessage.created_at > anchor.created_at,
                ChatMessage.id == anchor.id,
            ),
        )
    )
    conv.updated_at = datetime.now(timezone.utc)
    await db.commit()


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
