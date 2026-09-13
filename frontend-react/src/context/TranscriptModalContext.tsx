import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { apiFetch } from "../lib/api";
import { detectDir, formatTimestamp } from "../lib/format";
import { useToast } from "./ToastContext";
import type { TranscriptSegment } from "../types";

// Transcript modal — port of openTranscript/loadTranscript/closeModal/initModal,
// plus the deep-link + highlight behavior from openTranscriptAtSegment: it
// opens a video's transcript, scrolls to the matched segment and highlights it
// for 2.5s. Mounted once at the app root (inside ToastProvider) so every view
// can trigger it.

interface TranscriptModalContextValue {
  openTranscript: (videoId: string, title: string) => void;
  openTranscriptAtSegment: (
    videoId: string,
    videoTitle: string,
    segmentId: string,
    startTime: number
  ) => void;
}

const TranscriptModalContext = createContext<TranscriptModalContextValue>({
  openTranscript: () => {},
  openTranscriptAtSegment: () => {},
});

export function TranscriptModalProvider({ children }: { children: ReactNode }) {
  const { showToast } = useToast();
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [videoId, setVideoId] = useState<string | null>(null);
  const [withTimestamps, setWithTimestamps] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [segments, setSegments] = useState<TranscriptSegment[] | null>(null);
  const [plainText, setPlainText] = useState("");
  const [highlight, setHighlight] = useState<string | null>(null);

  const openTranscript = useCallback((id: string, videoTitle: string) => {
    setVideoId(id);
    setTitle(videoTitle);
    setWithTimestamps(true);
    setOpen(true);
    setHighlight(null);
  }, []);

  const openTranscriptAtSegment = useCallback(
    (id: string, videoTitle: string, segmentId: string, _startTime: number) => {
      setVideoId(id);
      setTitle(videoTitle);
      setWithTimestamps(true);
      setOpen(true);
      setHighlight(segmentId);
    },
    []
  );

  const closeModal = useCallback(() => {
    setOpen(false);
    setVideoId(null);
    setHighlight(null);
  }, []);

  // Load transcript whenever the open video or mode changes
  useEffect(() => {
    if (!open || !videoId) return;
    let cancelled = false;
    setLoading(true);
    setError(null);

    (async () => {
      try {
        if (withTimestamps) {
          const data = await apiFetch<TranscriptSegment[]>(
            `/api/videos/${videoId}/transcript?with_timestamps=true`
          );
          if (cancelled) return;
          setSegments(data);
          setPlainText(data.map((s) => s.text).join(" "));
        } else {
          const data = await apiFetch<{ text: string }>(
            `/api/videos/${videoId}/transcript?with_timestamps=false`
          );
          if (cancelled) return;
          setSegments(null);
          setPlainText(data.text);
        }
      } catch (err) {
        if (!cancelled) setError((err as Error).message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [open, videoId, withTimestamps]);

  // Scroll the highlighted segment into view after render
  useEffect(() => {
    if (!open || !highlight || loading) return;
    const element = document.querySelector(
      `.transcript-line[data-segment-id="${highlight}"]`
    );
    if (element) {
      element.scrollIntoView({ behavior: "smooth", block: "center" });
      const timer = window.setTimeout(() => setHighlight(null), 2500);
      return () => window.clearTimeout(timer);
    }
  }, [open, highlight, loading, segments]);

  // Escape key closes the modal
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeModal();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [open, closeModal]);

  const copyTranscript = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(plainText);
      showToast("Transcript copied to clipboard");
    } catch {
      showToast("Failed to copy text", "error");
    }
  }, [plainText, showToast]);

  return (
    <TranscriptModalContext.Provider
      value={{ openTranscript, openTranscriptAtSegment }}
    >
      {children}
      {open && (
        <div
          className="modal"
          role="dialog"
          aria-modal="true"
          aria-labelledby="transcript-title"
        >
          <div className="modal__backdrop" onClick={closeModal} />
          <div className="modal__content">
            <div className="modal__header">
              <h3 id="transcript-title" className="modal__title" dir="auto">
                {title}
              </h3>
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                onClick={closeModal}
                aria-label="Close"
              >
                ✕
              </button>
            </div>
            <div className="modal__toolbar">
              <div className="toggle-group" role="group" aria-label="Timestamp mode">
                <button
                  type="button"
                  className={`toggle-btn ${withTimestamps ? "toggle-btn--active" : ""}`}
                  onClick={() => setWithTimestamps(true)}
                >
                  With timestamps
                </button>
                <button
                  type="button"
                  className={`toggle-btn ${!withTimestamps ? "toggle-btn--active" : ""}`}
                  onClick={() => setWithTimestamps(false)}
                >
                  Without timestamps
                </button>
              </div>
              {!withTimestamps && (
                <button
                  type="button"
                  className="btn btn--ghost btn--sm"
                  onClick={copyTranscript}
                >
                  Copy text
                </button>
              )}
            </div>
            <div className="modal__body">
              {loading && <p className="empty-state">Loading transcript…</p>}
              {error && (
                <p className="empty-state" style={{ color: "var(--color-error)" }}>
                  {error}
                </p>
              )}
              {!loading && !error && withTimestamps && segments && (
                <>
                  {segments.length === 0 && (
                    <p className="empty-state">No transcript segments available.</p>
                  )}
                  {segments.map((seg) => {
                    const isLocal = seg.source_type === "local";
                    const link = isLocal ? seg.media_url : seg.youtube_link;
                    const linkTitle = isLocal ? "Play local video" : "Open on YouTube";
                    return (
                      <div
                        key={seg.segment_id}
                        className={`transcript-line ${
                          highlight === seg.segment_id
                            ? "transcript-line--highlight"
                            : ""
                        }`}
                        data-segment-id={seg.segment_id}
                      >
                        <a
                          href={link ?? undefined}
                          target="_blank"
                          rel="noopener"
                          className="transcript-ts"
                          title={linkTitle}
                        >
                          [{formatTimestamp(seg.start_time)}]
                        </a>
                        <span className="transcript-text" dir={detectDir(seg.text)}>
                          {seg.text}
                        </span>
                      </div>
                    );
                  })}
                </>
              )}
              {!loading && !error && !withTimestamps && (
                <p className="transcript-plain" dir={detectDir(plainText)}>
                  {plainText}
                </p>
              )}
            </div>
          </div>
        </div>
      )}
    </TranscriptModalContext.Provider>
  );
}

export function useTranscriptModal() {
  return useContext(TranscriptModalContext);
}
