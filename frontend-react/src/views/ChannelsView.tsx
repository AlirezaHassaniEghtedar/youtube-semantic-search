import { useCallback, useEffect, useMemo, useState } from "react";
import { apiFetch } from "../lib/api";
import {
  buildGoogleCalendarLink,
  detectDir,
  formatDate,
  formatDuration,
  formatScheduledDateTime,
} from "../lib/format";
import { selectSubtitleFile, selectVideoFile, hasNativeDialogs } from "../lib/pywebview";
import { useToast } from "../context/ToastContext";
import { useTranscriptModal } from "../context/TranscriptModalContext";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import type { Channel, SyncJob, Video } from "../types";

const ACTIVE_CHANNEL_STATUSES = new Set(["pending", "fetching_list", "processing"]);

// ── Add Channel form ────────────────────────────────────────────────────────

function AddChannelForm({ onAdded }: { onAdded: () => void }) {
  const { showToast } = useToast();
  const [url, setUrl] = useState("");
  const [timeWindow, setTimeWindow] = useState("7d");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [customHours, setCustomHours] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    try {
      const payload: Record<string, unknown> = {
        url: url.trim(),
        time_window: timeWindow,
      };
      if (timeWindow === "custom") {
        if (!startDate) {
          showToast("Please select a start date", "error");
          setLoading(false);
          return;
        }
        payload.start_date = new Date(startDate).toISOString();
        if (endDate) payload.end_date = new Date(endDate + "T23:59:59").toISOString();
      }
      if (timeWindow === "custom_hours") {
        const hours = parseInt(customHours, 10);
        if (!hours || hours < 1) {
          showToast("Please enter a valid number of hours", "error");
          setLoading(false);
          return;
        }
        payload.custom_hours = hours;
      }

      await apiFetch("/api/channels", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      showToast("Channel added — processing started");
      setUrl("");
      onAdded();
    } catch (err) {
      showToast((err as Error).message, "error");
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="card">
      <h2 className="card__title">Add Channel</h2>
      <form className="form" onSubmit={handleSubmit}>
        <div className="form__row">
          <label htmlFor="channel-url" className="form__label">Channel URL</label>
          <input
            type="url"
            id="channel-url"
            className="form__input"
            placeholder="https://www.youtube.com/@channelname"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            required
          />
        </div>
        <div className="form__row form__row--inline">
          <div className="form__field">
            <label htmlFor="time-window" className="form__label">Time Window</label>
            <select
              id="time-window"
              className="form__select"
              value={timeWindow}
              onChange={(e) => setTimeWindow(e.target.value)}
            >
              <option value="24h">Last 24 hours</option>
              <option value="7d">Last 7 days</option>
              <option value="30d">Last 30 days</option>
              <option value="custom">Custom range</option>
              <option value="custom_hours">Custom hours</option>
              <option value="all">All videos</option>
            </select>
          </div>
          {timeWindow === "custom" && (
            <div className="form__field custom-dates">
              <label htmlFor="start-date" className="form__label">Start Date</label>
              <input
                type="date"
                id="start-date"
                className="form__input"
                value={startDate}
                onChange={(e) => setStartDate(e.target.value)}
              />
              <label htmlFor="end-date" className="form__label">End Date</label>
              <input
                type="date"
                id="end-date"
                className="form__input"
                value={endDate}
                onChange={(e) => setEndDate(e.target.value)}
              />
            </div>
          )}
          {timeWindow === "custom_hours" && (
            <div className="form__field">
              <label htmlFor="custom-hours-input" className="form__label">Hours</label>
              <input
                type="number"
                id="custom-hours-input"
                className="form__input"
                min={1}
                max={8760}
                placeholder="e.g. 3"
                value={customHours}
                onChange={(e) => setCustomHours(e.target.value)}
              />
            </div>
          )}
        </div>
        <Button type="submit" loading={loading}>Add Channel</Button>
      </form>
    </section>
  );
}

// ── Add Local Video form (pywebview bridge) ─────────────────────────────────

function AddLocalVideoForm({ onAdded }: { onAdded: () => void }) {
  const { showToast } = useToast();
  const [videoPath, setVideoPath] = useState("");
  const [subtitlePath, setSubtitlePath] = useState("");
  const [title, setTitle] = useState("");
  const [loading, setLoading] = useState(false);
  // Vanilla behavior: Browse buttons (pywebview native dialogs) only render
  // inside the pywebview shell — a plain browser has no bridge to call.
  const [nativeDialogs, setNativeDialogs] = useState(hasNativeDialogs);

  useEffect(() => {
    // pywebview injects the bridge asynchronously after page load
    const timer = window.setInterval(() => {
      if (hasNativeDialogs()) {
        setNativeDialogs(true);
        window.clearInterval(timer);
      }
    }, 400);
    return () => window.clearInterval(timer);
  }, []);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!videoPath.trim()) {
      showToast("Please choose a video file", "error");
      return;
    }
    setLoading(true);
    try {
      await apiFetch("/api/local-videos", {
        method: "POST",
        body: JSON.stringify({
          video_path: videoPath.trim(),
          subtitle_path: subtitlePath.trim() || null,
          title: title.trim() || null,
        }),
      });
      showToast(
        subtitlePath.trim()
          ? "Local video added — indexing in the background"
          : "Local video added — looking for subtitles, or transcribing with Whisper in the background"
      );
      setVideoPath("");
      setSubtitlePath("");
      setTitle("");
      onAdded();
    } catch (err) {
      showToast((err as Error).message, "error");
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="card">
      <h2 className="card__title">Add Local Video</h2>
      <p className="card__subtitle">
        Index an offline video file — it uses an embedded subtitle track or a matching
        .srt/.vtt file if either exists, otherwise it transcribes the audio with Whisper.
        It will appear alongside your channels and show up in search results.
      </p>
      <form className="form" onSubmit={handleSubmit}>
        <div className="form__row">
          <label htmlFor="local-video-path" className="form__label">Video file</label>
          <div className="form__row--inline">
            <input
              type="text"
              id="local-video-path"
              className="form__input"
              placeholder="Path to video file (.mp4, .mkv, .webm, …)"
              value={videoPath}
              onChange={(e) => setVideoPath(e.target.value)}
              required
            />
            {nativeDialogs && (
              <Button
                variant="ghost"
                onClick={async () => {
                  const path = await selectVideoFile();
                  if (path) setVideoPath(path);
                }}
              >
                Browse…
              </Button>
            )}
          </div>
        </div>
        <div className="form__row">
          <label htmlFor="local-subtitle-path" className="form__label">Subtitle file (optional)</label>
          <div className="form__row--inline">
            <input
              type="text"
              id="local-subtitle-path"
              className="form__input"
              placeholder="Leave blank to auto-detect or transcribe with Whisper"
              value={subtitlePath}
              onChange={(e) => setSubtitlePath(e.target.value)}
            />
            {nativeDialogs && (
              <Button
                variant="ghost"
                onClick={async () => {
                  const path = await selectSubtitleFile();
                  if (path) setSubtitlePath(path);
                }}
              >
                Browse…
              </Button>
            )}
          </div>
        </div>
        <div className="form__row">
          <label htmlFor="local-video-title" className="form__label">Title (optional)</label>
          <input
            type="text"
            id="local-video-title"
            className="form__input"
            placeholder="Defaults to the video file name"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
        </div>
        <Button type="submit" loading={loading}>Add Local Video</Button>
      </form>
    </section>
  );
}

// ── Channel list ────────────────────────────────────────────────────────────

interface ChannelListProps {
  channels: Channel[];
  selectedChannelId: string | null;
  onSelect: (id: string, name: string) => void;
  onChanged: () => void;
}

function ChannelList({ channels, selectedChannelId, onSelect, onChanged }: ChannelListProps) {
  const { showToast } = useToast();

  async function handleStop(id: string) {
    try {
      await apiFetch(`/api/channels/${id}/stop`, { method: "POST" });
      showToast("Stop requested — finishing the current step, then stopping");
      onChanged();
    } catch (err) {
      showToast((err as Error).message, "error");
    }
  }

  async function handleDelete(id: string) {
    if (!window.confirm("Delete this channel and all its data?")) return;
    try {
      await apiFetch(`/api/channels/${id}`, { method: "DELETE" });
      showToast("Channel deleted");
      onChanged();
    } catch (err) {
      showToast((err as Error).message, "error");
    }
  }

  if (!channels.length) {
    return (
      <p className="empty-state">
        No channels added yet. Add a YouTube channel above to get started.
      </p>
    );
  }

  return (
    <div className="channels-list">
      {channels.map((ch) => (
        <div key={ch.id} className="channel-card">
          <div className="channel-card__info">
            <div className="channel-card__name">{ch.name || ch.url}</div>
            <div className="channel-card__meta">
              <Badge status={ch.status} />
              <span>{ch.done_videos}/{ch.total_videos} done</span>
            </div>
          </div>
          <div className="channel-card__actions">
            {ACTIVE_CHANNEL_STATUSES.has(ch.status) && (
              <Button variant="warning" size="sm" onClick={() => handleStop(ch.id)}>
                Stop Syncing
              </Button>
            )}
            <Button
              variant="ghost"
              size="sm"
              className={selectedChannelId === ch.id ? "btn--active-ghost" : ""}
              onClick={() => onSelect(ch.id, ch.name || ch.url)}
            >
              View Videos
            </Button>
            <Button variant="danger" size="sm" onClick={() => handleDelete(ch.id)}>
              Delete
            </Button>
          </div>
        </div>
      ))}
    </div>
  );
}

// ── Videos table + sync history ─────────────────────────────────────────────

// Video-type sort order for the "Sort by type" option. Upcoming events first
// (time-sensitive — the user is most likely watching for a scheduled stream),
// then streamed videos and regular long videos newest-first within their
// groups, shorts last (quick, low-information content). Ties inside each group
// keep the date order so grouping never looks random.
const VIDEO_TYPE_SORT_ORDER = [
  "upcoming event",
  "streamed video",
  "long video",
  "short video",
];

const VIDEO_TYPE_FILTER_OPTIONS = [
  "long video",
  "short video",
  "streamed video",
  "upcoming event",
] as const;

type SortMode = "date-desc" | "date-asc" | "type";

function sortVideos(videos: Video[], mode: SortMode): Video[] {
  const sorted = [...videos];
  if (mode === "date-desc" || mode === "date-asc") {
    const direction = mode === "date-desc" ? -1 : 1;
    sorted.sort((a, b) => {
      // Videos with no published_at always sort to the END regardless of
      // direction, so they land in a predictable place instead of bouncing
      // around (NaN comparisons would leave their position arbitrary).
      if (!a.published_at && !b.published_at) return 0;
      if (!a.published_at) return 1;
      if (!b.published_at) return -1;
      return direction * a.published_at.localeCompare(b.published_at);
    });
  } else {
    const rank = (t: string | null) => {
      const idx = t ? VIDEO_TYPE_SORT_ORDER.indexOf(t) : -1;
      return idx === -1 ? VIDEO_TYPE_SORT_ORDER.length : idx;
    };
    sorted.sort((a, b) => {
      const byType = rank(a.video_type) - rank(b.video_type);
      if (byType !== 0) return byType;
      // Within the same type: newest first (ISO strings compare correctly).
      if (!a.published_at && !b.published_at) return 0;
      if (!a.published_at) return 1;
      if (!b.published_at) return -1;
      return b.published_at.localeCompare(a.published_at);
    });
  }
  return sorted;
}

function VideosPanel({
  channelId,
  channelName,
  anyActive,
  onClose,
}: {
  channelId: string;
  channelName: string;
  anyActive: boolean;
  onClose: () => void;
}) {
  const [videos, setVideos] = useState<Video[]>([]);
  const [jobs, setJobs] = useState<SyncJob[]>([]);
  const [sortMode, setSortMode] = useState<SortMode>("date-desc");
  // Empty set = no filter: every type is visible by default.
  const [typeFilter, setTypeFilter] = useState<Set<string>>(new Set());

  // Filter first, then sort — pure client-side operations on the fetched
  // array, so they compose and update instantly without any re-fetch.
  const visibleVideos = useMemo(() => {
    const filtered =
      typeFilter.size === 0
        ? videos
        : videos.filter((v) => v.video_type !== null && typeFilter.has(v.video_type));
    return sortVideos(filtered, sortMode);
  }, [videos, sortMode, typeFilter]);

  function toggleTypeFilter(type: string) {
    setTypeFilter((prev) => {
      const next = new Set(prev);
      if (next.has(type)) {
        next.delete(type);
      } else {
        next.add(type);
      }
      return next;
    });
  }

  const refresh = useCallback(async () => {
    try {
      const [v, j] = await Promise.all([
        apiFetch<Video[]>(`/api/channels/${channelId}/videos`),
        apiFetch<SyncJob[]>(`/api/channels/${channelId}/syncs`),
      ]);
      setVideos(v);
      setJobs(j);
    } catch (err) {
      console.error("Failed to load videos/syncs:", err);
    }
  }, [channelId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Live-update statuses while a sync is running (vanilla startPolling did
  // this for the open channel's videos + sync history).
  useEffect(() => {
    if (!anyActive) return;
    const interval = setInterval(() => {
      void refresh();
    }, 3000);
    return () => clearInterval(interval);
  }, [anyActive, refresh]);

  return (
    <section className="card">
      <div className="card__header">
        <h2 className="card__title">Videos — {channelName}</h2>
        <Button variant="ghost" size="sm" onClick={onClose}>Close</Button>
      </div>
      <div className="videos-toolbar">
        <div className="form__field videos-toolbar__sort">
          <label htmlFor="videos-sort" className="form__label">Sort</label>
          <select
            id="videos-sort"
            className="form__select"
            value={sortMode}
            onChange={(e) => setSortMode(e.target.value as SortMode)}
          >
            <option value="date-desc">Date (newest first)</option>
            <option value="date-asc">Date (oldest first)</option>
            <option value="type">Video type</option>
          </select>
        </div>
        <div className="form__field videos-toolbar__filter">
          <span className="form__label">Filter by type</span>
          <div className="toggle-group" role="group" aria-label="Filter by video type">
            {VIDEO_TYPE_FILTER_OPTIONS.map((type) => (
              <button
                key={type}
                type="button"
                className={`toggle-btn ${typeFilter.has(type) ? "toggle-btn--active" : ""}`}
                aria-pressed={typeFilter.has(type)}
                onClick={() => toggleTypeFilter(type)}
              >
                {type}
              </button>
            ))}
          </div>
        </div>
      </div>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>Title</th>
              <th>Type</th>
              <th>Published</th>
              <th>Duration</th>
              <th>Status</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {videos.length === 0 ? (
              <tr>
                <td colSpan={6} className="empty-state">No videos in this time window.</td>
              </tr>
            ) : visibleVideos.length === 0 ? (
              <tr>
                <td colSpan={6} className="empty-state">No videos match the selected type filter.</td>
              </tr>
            ) : (
              visibleVideos.map((v) => (
                <VideoRow key={v.id} video={v} />
              ))
            )}
          </tbody>
        </table>
      </div>
      <div className="sync-history">
        <h3 className="sync-history__title">Sync history</h3>
        <div className="sync-history__list">
          {jobs.length === 0 ? (
            <p className="empty-state">No syncs yet.</p>
          ) : (
            jobs.map((job) => (
              <div key={job.id} className="sync-history__item">
                <Badge status={job.status} />
                <span>{job.time_window}</span>
                <span>
                  {job.status === "skipped_already_covered"
                    ? "already covered"
                    : `${job.new_videos_found} new videos`}
                </span>
                <span>{formatDate(job.created_at)}</span>
              </div>
            ))
          )}
        </div>
      </div>
    </section>
  );
}

function VideoRow({ video }: { video: Video }) {
  const { openTranscript } = useTranscriptModal();

  if (video.video_type === "upcoming event") {
    const youtubeUrl = `https://www.youtube.com/watch?v=${video.youtube_video_id}`;
    const calendarLink = buildGoogleCalendarLink(
      video.title,
      video.scheduled_start_at,
      youtubeUrl
    );
    return (
      <tr>
        <td className="video-title" title={video.title} dir={detectDir(video.title)}>
          {video.title}
        </td>
        <td>{video.video_type ? <Badge status={video.video_type} kind="video-type" /> : "—"}</td>
        <td>{formatDate(video.published_at)}</td>
        <td>{formatDuration(video.duration_seconds)}</td>
        <td><Badge status={video.status} /></td>
        <td>
          <div className="upcoming-event__actions">
            <span className="upcoming-event__schedule">
              {formatScheduledDateTime(video.scheduled_start_at)}
            </span>
            <a
              href={youtubeUrl}
              target="_blank"
              rel="noopener"
              className="btn btn--ghost btn--sm"
            >
              Watch on YouTube
            </a>
            {calendarLink && (
              <a
                href={calendarLink}
                target="_blank"
                rel="noopener"
                className="btn btn--ghost btn--sm"
              >
                Add to Google Calendar
              </a>
            )}
          </div>
        </td>
      </tr>
    );
  }

  return (
    <tr>
      <td className="video-title" title={video.title} dir={detectDir(video.title)}>
        {video.title}
      </td>
      <td>{video.video_type ? <Badge status={video.video_type} kind="video-type" /> : "—"}</td>
      <td>{formatDate(video.published_at)}</td>
      <td>{formatDuration(video.duration_seconds)}</td>
      <td><Badge status={video.status} /></td>
      <td>
        {video.status === "done" ? (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => openTranscript(video.id, video.title)}
          >
            View Transcript
          </Button>
        ) : video.error_message ? (
          <span
            title={video.error_message}
            style={{ color: "var(--color-error)", fontSize: "0.8rem" }}
          >
            Error
          </span>
        ) : (
          "—"
        )}
      </td>
    </tr>
  );
}

// ── ChannelsView ────────────────────────────────────────────────────────────

interface ChannelsViewProps {
  // Shared channel state owned by AppShell's single useChannels() instance.
  // Owning it here too would create a second, independent state instance with
  // its own polling loop, and the lists AppShell passes to SearchView/ChatView
  // could go stale after a sync changed things on this view.
  channels: Channel[];
  anyActive: boolean;
  refresh: () => Promise<unknown>;
}

export function ChannelsView({ channels, anyActive, refresh }: ChannelsViewProps) {
  const [selected, setSelected] = useState<{ id: string; name: string } | null>(null);

  const handleChanged = useCallback(() => {
    void refresh();
  }, [refresh]);

  // Close the videos panel if its channel disappears (e.g. deleted)
  useEffect(() => {
    if (selected && !channels.some((c) => c.id === selected.id)) {
      setSelected(null);
    }
  }, [channels, selected]);

  return (
    <div className="view-stack">
      <AddChannelForm onAdded={handleChanged} />
      <AddLocalVideoForm onAdded={handleChanged} />
      <section className="card">
        <div className="card__header">
          <h2 className="card__title">Channels</h2>
          {anyActive && (
            <span className="polling-indicator" aria-live="polite">
              <span className="polling-dot" /> Syncing…
            </span>
          )}
        </div>
        <ChannelList
          channels={channels}
          selectedChannelId={selected?.id ?? null}
          onSelect={(id, name) => setSelected({ id, name })}
          onChanged={handleChanged}
        />
      </section>
      {selected && (
        <VideosPanel
          channelId={selected.id}
          channelName={selected.name}
          anyActive={anyActive}
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  );
}
