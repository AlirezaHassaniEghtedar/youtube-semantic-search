import { useTranscriptModal } from "../context/TranscriptModalContext";
import { detectDir, formatTimestamp } from "../lib/format";
import type { SearchResult } from "../types";

// Shared result card — the SINGLE component used by both standalone search
// results and chat message sources (requirement: no divergent implementations).

export function ResultCard({ result }: { result: SearchResult }) {
  const { openTranscriptAtSegment } = useTranscriptModal();
  const dir = detectDir(result.text);
  const pct = Math.round(result.similarity * 100);
  const isLocal = result.source_type === "local";
  const watchLink = isLocal ? result.media_url : result.youtube_link;
  const watchLabel = isLocal ? "▶ Play" : "▶ Watch";

  return (
    <div className="result-card">
      <div className="result-card__header">
        <div>
          <div className="result-card__channel">{result.channel_name}</div>
          <div className="result-card__title" dir={detectDir(result.video_title)}>
            {result.video_title}
          </div>
        </div>
        <span className="similarity">{pct}% match</span>
      </div>
      <p className="result-card__snippet" dir={dir}>
        {result.text}
      </p>
      <div className="result-card__footer">
        <button
          type="button"
          className="btn btn--ghost btn--sm"
          onClick={() =>
            openTranscriptAtSegment(
              result.video_id,
              result.video_title,
              result.segment_id,
              result.start_time
            )
          }
        >
          View in transcript
        </button>
        <span>⏱ {formatTimestamp(result.start_time)}</span>
        <a
          href={watchLink ?? undefined}
          target="_blank"
          rel="noopener"
          className="btn btn--primary btn--sm"
        >
          {watchLabel}
        </a>
      </div>
    </div>
  );
}
