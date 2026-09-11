import json
import logging
from uuid import UUID

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.schemas import ChatResponse, SearchResult
from app.services.embedder import EmbedderService
from app.services.retrieval import retrieve_segments

logger = logging.getLogger(__name__)

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


def _is_persian(text: str) -> bool:
    """Detect if text contains Persian characters."""
    return any("\u0600" <= c <= "\u06FF" for c in text)


def _build_retrieval_context(segments: list[SearchResult]) -> str:
    """Build numbered segment list for Gemini prompt."""
    if not segments:
        return ""
    
    lines = []
    for i, seg in enumerate(segments):
        lines.append(
            f"[Segment {i}] Video: {seg.video_title} | Channel: {seg.channel_name} | "
            f"Time: {seg.start_time:.0f}s\n{seg.text}"
        )
    return "\n\n".join(lines)


async def generate_conversation_title(question: str) -> str:
    """Generate a concise 3-6 word title for a conversation using Gemini."""
    if not settings.GEMINI_API_KEY:
        # Fallback to truncation
        return (question[:50] + "...") if len(question) > 50 else question
    
    try:
        prompt = f'Create a concise 3-6 word title for this user question (respond ONLY with the title, no punctuation or quotes):\n\n{question}'
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{GEMINI_API_BASE}/{settings.GEMINI_MODEL}:generateContent",
                params={"key": settings.GEMINI_API_KEY},
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "temperature": 0.7,
                        "maxOutputTokens": 20,
                    }
                },
            )
        
        if response.status_code == 200:
            data = response.json()
            candidates = data.get("candidates", [])
            if candidates:
                content = candidates[0].get("content", {})
                parts = content.get("parts", [])
                if parts:
                    title = parts[0].get("text", "").strip()
                    if title:
                        return title
    except Exception as e:
        logger.debug(f"Title generation failed, using fallback: {e}")
    
    # Fallback to truncation
    return (question[:50] + "...") if len(question) > 50 else question



    """Build the prompt for Gemini with structured output instruction."""
    language = "Persian" if _is_persian(question) else "English"
    
    if not context:
        # No segments retrieved
        return f"""You are a helpful assistant analyzing video transcripts.

Question: {question}

No relevant video segments were found in the database. Please respond honestly that this topic was not discussed.

Respond ONLY in valid JSON with this exact structure (respond in {language}):
{{"found_relevant_content": false, "answer": "The topic was not discussed in the available videos", "relevant_segment_indices": []}}

Do NOT include markdown code fences or any text outside the JSON object."""
    
    return f"""You are a helpful assistant analyzing video transcripts. Answer questions based ONLY on the provided video segments.

Question (in {language}): {question}

Available video segments:

{context}

INSTRUCTIONS:
1. Answer the question ONLY using information from the provided segments above
2. If the segments do not contain relevant information to answer the question, set found_relevant_content to false
3. Identify which segments are relevant to the question by their [Segment N] index
4. Respond in {language}, the same language as the question
5. Be concise and factual

Respond ONLY in valid JSON with this exact structure:
{{"found_relevant_content": true or false, "answer": "your answer here", "relevant_segment_indices": [list of segment indices that support your answer]}}

Do NOT include markdown code fences or any text outside the JSON object."""


async def answer_question(
    question: str,
    db: AsyncSession,
    embedder: EmbedderService,
    channel_id: UUID | None = None,
    top_k: int = 8,
) -> ChatResponse:
    """
    Answer a question using RAG: retrieve relevant segments, ask Gemini,
    parse response to determine which segments are relevant.
    
    Returns ChatResponse with answer and sources (subset of retrieved segments
    that Gemini determined are actually relevant).
    """
    
    if not settings.GEMINI_API_KEY:
        return ChatResponse(
            answer="⚠️ Chat is not configured. Please set GEMINI_API_KEY in your .env file. Get a free key at https://aistudio.google.com/apikey",
            sources=[],
        )
    
    if not question.strip():
        return ChatResponse(
            answer="Please ask a question.",
            sources=[],
        )
    
    try:
        # Retrieve candidate segments
        segments = await retrieve_segments(
            db=db,
            embedder=embedder,
            query=question,
            channel_id=channel_id,
            top_k=top_k,
        )
        
        context = _build_retrieval_context(segments)
        prompt = _build_gemini_prompt(question, context, segments)
        
        # Call Gemini API
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{GEMINI_API_BASE}/{settings.GEMINI_MODEL}:generateContent",
                params={"key": settings.GEMINI_API_KEY},
                json={
                    "contents": [
                        {
                            "parts": [
                                {"text": prompt}
                            ]
                        }
                    ],
                    "generationConfig": {
                        "temperature": 0.7,
                        "maxOutputTokens": 1024,
                    }
                },
            )
        
        if response.status_code == 429:
            # Quota exceeded
            return ChatResponse(
                answer="⏱️ Chat is temporarily unavailable due to API rate limits. Please try again in a moment.",
                sources=[],
            )
        
        if response.status_code == 401 or response.status_code == 403:
            # Invalid API key
            return ChatResponse(
                answer="⚠️ Invalid or expired GEMINI_API_KEY. Please check your .env file and restart the app. Get a free key at https://aistudio.google.com/apikey",
                sources=[],
            )
        
        if response.status_code >= 400:
            logger.error(f"Gemini API error {response.status_code}: {response.text}")
            return ChatResponse(
                answer=f"❌ Chat service error: {response.status_code}. Please try again later.",
                sources=[],
            )
        
        # Parse response
        data = response.json()
        candidates = data.get("candidates", [])
        if not candidates:
            logger.warning("Gemini returned no candidates")
            return ChatResponse(
                answer="Sorry, I couldn't generate a response. Please try again.",
                sources=[],
            )
        
        content = candidates[0].get("content", {})
        parts = content.get("parts", [])
        if not parts:
            logger.warning("Gemini returned no text content")
            return ChatResponse(
                answer="Sorry, I couldn't generate a response. Please try again.",
                sources=[],
            )
        
        text = parts[0].get("text", "").strip()
        
        # Extract JSON from response (handle markdown code fences)
        if text.startswith("```"):
            # Remove markdown code fences
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
        
        parsed = json.loads(text)
        
        answer = parsed.get("answer", "")
        found_relevant = parsed.get("found_relevant_content", False)
        relevant_indices = parsed.get("relevant_segment_indices", [])
        
        # Filter to only relevant sources
        sources = []
        if found_relevant and relevant_indices:
            for idx in relevant_indices:
                if isinstance(idx, int) and 0 <= idx < len(segments):
                    sources.append(segments[idx])
        
        return ChatResponse(
            answer=answer,
            sources=sources,
        )
    
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse Gemini response as JSON: {e}")
        return ChatResponse(
            answer="Sorry, I encountered an issue processing the response. Please try again.",
            sources=[],
        )
    except httpx.ConnectError:
        logger.error("Failed to connect to Gemini API")
        return ChatResponse(
            answer="❌ Network error: Unable to reach the chat service. Please check your internet connection.",
            sources=[],
        )
    except httpx.TimeoutException:
        logger.error("Gemini API request timed out")
        return ChatResponse(
            answer="⏱️ Chat service is taking too long to respond. Please try again.",
            sources=[],
        )
    except Exception as e:
        logger.error(f"Unexpected error in chat: {e}")
        return ChatResponse(
            answer=f"❌ Unexpected error: {str(e)}",
            sources=[],
        )
