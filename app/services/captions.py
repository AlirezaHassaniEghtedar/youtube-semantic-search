import logging
from typing import Any

from youtube_transcript_api import (
    NoTranscriptFound,
    TranscriptsDisabled,
    VideoUnavailable,
    YouTubeTranscriptApi,
)

logger = logging.getLogger(__name__)

PREFERRED_LANGUAGES = ["fa", "en"]


def fetch_captions(youtube_video_id: str) -> list[dict[str, Any]] | None:
    """Fetch YouTube captions in the same shape as Whisper transcription."""
    try:
        # youtube-transcript-api 1.x changed this from a class method to an
        # instance method; support both forms so dependency upgrades do not
        # disable the optional captions fast path.
        if hasattr(YouTubeTranscriptApi, "list_transcripts"):
            transcript_list = YouTubeTranscriptApi.list_transcripts(youtube_video_id)
        else:
            transcript_list = YouTubeTranscriptApi().list(youtube_video_id)

        transcript = None
        for language in PREFERRED_LANGUAGES:
            try:
                transcript = transcript_list.find_transcript([language])
                break
            except NoTranscriptFound:
                continue

        if transcript is None:
            try:
                transcript = transcript_list.find_generated_transcript(
                    PREFERRED_LANGUAGES
                )
            except NoTranscriptFound:
                logger.info("No captions available for %s", youtube_video_id)
                return None

        fetched = transcript.fetch()
        raw = fetched.to_raw_data() if hasattr(fetched, "to_raw_data") else fetched
    except (TranscriptsDisabled, VideoUnavailable, NoTranscriptFound):
        logger.info("Captions disabled/unavailable for %s", youtube_video_id)
        return None
    except Exception:
        logger.exception("Unexpected error fetching captions for %s", youtube_video_id)
        return None

    segments: list[dict[str, Any]] = []
    for entry in raw:
        start = float(entry["start"])
        duration = float(entry.get("duration", 0.0))
        text = entry["text"].strip()
        if text:
            segments.append({"start": start, "end": start + duration, "text": text})

    if not segments:
        return None

    logger.info("Fetched %d caption segments for %s", len(segments), youtube_video_id)
    return segments
