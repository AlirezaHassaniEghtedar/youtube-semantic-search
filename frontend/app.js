/* YouTube Semantic Search — Frontend Application */

const TERMINAL_CHANNEL_STATUSES = new Set(["done", "error", "stopped"]);
const ACTIVE_CHANNEL_STATUSES = new Set(["pending", "fetching_list", "processing"]);
const TERMINAL_VIDEO_STATUSES = new Set(["done", "error"]);
const POLL_INTERVAL_MS = 3000;

let pollInterval = null;
let selectedChannelId = null;
let currentVideoId = null;
let transcriptWithTimestamps = true;
let plainTranscriptText = "";

// ── Utilities ──────────────────────────────────────────────────────────────

function isPersian(text) {
  return /[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]/.test(text);
}

function detectDir(text) {
  return isPersian(text) ? "rtl" : "ltr";
}

function formatDuration(seconds) {
  if (seconds == null) return "—";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h > 0) {
    return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  }
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function formatTimestamp(seconds) {
  return formatDuration(Math.floor(seconds));
}

function formatDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

function formatScheduledDateTime(iso) {
  if (!iso) return "Schedule to be announced";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "Schedule to be announced";
  return date.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  });
}

function buildGoogleCalendarLink(title, scheduledStartIso, youtubeUrl) {
  const start = new Date(scheduledStartIso);
  if (Number.isNaN(start.getTime())) return null;
  const end = new Date(start.getTime() + 60 * 60 * 1000);
  const toCalendarUtc = (date) => {
    const pad = (value) => String(value).padStart(2, "0");
    return `${date.getUTCFullYear()}${pad(date.getUTCMonth() + 1)}${pad(date.getUTCDate())}T${pad(date.getUTCHours())}${pad(date.getUTCMinutes())}${pad(date.getUTCSeconds())}Z`;
  };
  const params = new URLSearchParams({
    action: "TEMPLATE",
    text: title,
    dates: `${toCalendarUtc(start)}/${toCalendarUtc(end)}`,
    details: youtubeUrl,
  });
  return `https://calendar.google.com/calendar/render?${params.toString()}`;
}

function showToast(message, type = "success") {
  const container = document.getElementById("toast-container");
  const toast = document.createElement("div");
  toast.className = `toast toast--${type}`;
  toast.textContent = message;
  container.appendChild(toast);
  setTimeout(() => toast.remove(), 4000);
}

function setButtonLoading(btn, loading) {
  if (loading) {
    btn.classList.add("btn--loading");
    btn.disabled = true;
    btn.querySelector(".btn__spinner")?.classList.remove("hidden");
  } else {
    btn.classList.remove("btn--loading");
    btn.disabled = false;
    btn.querySelector(".btn__spinner")?.classList.add("hidden");
  }
}

async function apiFetch(url, options = {}) {
  const resp = await fetch(url, {
    headers: { "Content-Type": "application/json", ...options.headers },
    ...options,
  });
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      const err = await resp.json();
      detail = err.detail || detail;
    } catch (_) { /* ignore */ }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  if (resp.status === 204) return null;
  return resp.json();
}

// ── Theme ──────────────────────────────────────────────────────────────────

function initTheme() {
  const saved = localStorage.getItem("theme") || "light";
  document.documentElement.setAttribute("data-theme", saved);

  document.getElementById("theme-toggle").addEventListener("click", () => {
    const current = document.documentElement.getAttribute("data-theme");
    const next = current === "light" ? "dark" : "light";
    document.documentElement.setAttribute("data-theme", next);
    localStorage.setItem("theme", next);
  });
}

// ── Polling ────────────────────────────────────────────────────────────────

function startPolling() {
  if (pollInterval) return;
  document.getElementById("polling-indicator").classList.remove("hidden");
  pollInterval = setInterval(async () => {
    await refreshChannels();
    if (selectedChannelId) {
      await refreshVideos(selectedChannelId);
      await refreshSyncHistory(selectedChannelId);
    }
    checkStopPolling();
  }, POLL_INTERVAL_MS);
}

function stopPolling() {
  if (pollInterval) {
    clearInterval(pollInterval);
    pollInterval = null;
  }
  document.getElementById("polling-indicator").classList.add("hidden");
}

function checkStopPolling(channels) {
  if (!channels) return;
  const allTerminal = channels.every((c) => TERMINAL_CHANNEL_STATUSES.has(c.status));
  if (allTerminal) {
    stopPolling();
  }
}

// ── Channels ─────────────────────────────────────────────────────────────

async function refreshChannels() {
  try {
    const channels = await apiFetch("/api/channels");
    renderChannels(channels);
    updateSearchChannelFilter(channels);
    checkStopPolling(channels);

    const anyActive = channels.some((c) => !TERMINAL_CHANNEL_STATUSES.has(c.status));
    if (anyActive && !pollInterval) startPolling();

    return channels;
  } catch (err) {
    console.error("Failed to load channels:", err);
  }
}

function renderChannels(channels) {
  const container = document.getElementById("channels-list");

  if (!channels.length) {
    container.innerHTML =
      '<p class="empty-state">No channels added yet. Add a YouTube channel above to get started.</p>';
    return;
  }

  container.innerHTML = channels
    .map(
      (ch) => `
    <div class="channel-card" data-id="${ch.id}">
      <div class="channel-card__info">
        <div class="channel-card__name">${escapeHtml(ch.name || ch.url)}</div>
        <div class="channel-card__meta">
          <span class="badge badge--${ch.status}">${ch.status.replace(/_/g, " ")}</span>
          <span>${ch.done_videos}/${ch.total_videos} done</span>
        </div>
      </div>
      <div class="channel-card__actions">
        ${
          ACTIVE_CHANNEL_STATUSES.has(ch.status)
            ? `<button type="button" class="btn btn--warning btn--sm stop-sync-btn" data-id="${ch.id}">Stop Syncing</button>`
            : ""
        }
        <button type="button" class="btn btn--ghost btn--sm view-videos-btn" data-id="${ch.id}" data-name="${escapeHtml(ch.name || ch.url)}">
          View Videos
        </button>
        <button type="button" class="btn btn--danger btn--sm delete-channel-btn" data-id="${ch.id}">
          Delete
        </button>
      </div>
    </div>`
    )
    .join("");

  container.querySelectorAll(".view-videos-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      selectedChannelId = btn.dataset.id;
      document.getElementById("selected-channel-name").textContent = btn.dataset.name;
      document.getElementById("videos-section").classList.remove("hidden");
      refreshVideos(selectedChannelId);
      refreshSyncHistory(selectedChannelId);
    });
  });

  container.querySelectorAll(".stop-sync-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      try {
        await apiFetch(`/api/channels/${btn.dataset.id}/stop`, { method: "POST" });
        showToast("Stop requested — finishing the current step, then stopping");
        await refreshChannels();
      } catch (err) {
        showToast(err.message, "error");
        btn.disabled = false;
      }
    });
  });

  container.querySelectorAll(".delete-channel-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      if (!confirm("Delete this channel and all its data?")) return;
      try {
        await apiFetch(`/api/channels/${btn.dataset.id}`, { method: "DELETE" });
        if (selectedChannelId === btn.dataset.id) {
          selectedChannelId = null;
          document.getElementById("videos-section").classList.add("hidden");
        }
        showToast("Channel deleted");
        await refreshChannels();
      } catch (err) {
        showToast(err.message, "error");
      }
    });
  });
}

function updateSearchChannelFilter(channels) {
  const select = document.getElementById("search-channel");
  const current = select.value;
  select.innerHTML = '<option value="">All channels</option>';
  channels.forEach((ch) => {
    const opt = document.createElement("option");
    opt.value = ch.id;
    opt.textContent = ch.name || ch.url;
    select.appendChild(opt);
  });
  if (current) select.value = current;
}

// ── Videos ─────────────────────────────────────────────────────────────────

async function refreshVideos(channelId) {
  try {
    const videos = await apiFetch(`/api/channels/${channelId}/videos`);
    renderVideos(videos);
  } catch (err) {
    console.error("Failed to load videos:", err);
  }
}

async function refreshSyncHistory(channelId) {
  const container = document.getElementById("sync-history-list");
  try {
    const jobs = await apiFetch(`/api/channels/${channelId}/syncs`);
    container.innerHTML = jobs.length
      ? jobs.map((job) => `
          <div class="sync-history__item">
            <span class="badge badge--${job.status}">${job.status.replace(/_/g, " ")}</span>
            <span>${escapeHtml(job.time_window)}</span>
            <span>${job.status === "skipped_already_covered" ? "already covered" : `${job.new_videos_found} new videos`}</span>
            <span>${formatDate(job.created_at)}</span>
          </div>`).join("")
      : '<p class="empty-state">No syncs yet.</p>';
  } catch (err) {
    console.error("Failed to load sync history:", err);
  }
}

function renderVideos(videos) {
  const tbody = document.getElementById("videos-tbody");

  if (!videos.length) {
    tbody.innerHTML =
      '<tr><td colspan="5" class="empty-state">No videos in this time window.</td></tr>';
    return;
  }

  tbody.innerHTML = videos
    .map(
      (v) => `
    <tr>
      <td class="video-title" title="${escapeHtml(v.title)}" dir="${detectDir(v.title)}">${escapeHtml(v.title)}${v.video_type ? ` <span class="video-type-badge">${escapeHtml(v.video_type)}</span>` : ""}</td>
      <td>${formatDate(v.published_at)}</td>
      <td>${formatDuration(v.duration_seconds)}</td>
      <td><span class="badge badge--${v.status}">${v.status}</span></td>
      <td>
        ${
          v.video_type === "upcoming event"
            ? `<div class="upcoming-event__actions">
                <span class="upcoming-event__schedule">${formatScheduledDateTime(v.scheduled_start_at)}</span>
                <a href="https://www.youtube.com/watch?v=${v.youtube_video_id}" target="_blank" rel="noopener" class="btn btn--ghost btn--sm">Watch on YouTube</a>
                ${buildGoogleCalendarLink(v.title, v.scheduled_start_at, `https://www.youtube.com/watch?v=${v.youtube_video_id}`) ? `<a href="${buildGoogleCalendarLink(v.title, v.scheduled_start_at, `https://www.youtube.com/watch?v=${v.youtube_video_id}`)}" target="_blank" rel="noopener" class="btn btn--ghost btn--sm">Add to Google Calendar</a>` : ""}
              </div>`
            : v.status === "done"
            ? `<button type="button" class="btn btn--ghost btn--sm view-transcript-btn" data-id="${v.id}" data-title="${escapeHtml(v.title)}">View Transcript</button>`
            : v.error_message
              ? `<span title="${escapeHtml(v.error_message)}" style="color:var(--color-error);font-size:0.8rem">Error</span>`
              : "—"
        }
      </td>
    </tr>`
    )
    .join("");

  tbody.querySelectorAll(".view-transcript-btn").forEach((btn) => {
    btn.addEventListener("click", () => openTranscript(btn.dataset.id, btn.dataset.title));
  });
}

// ── Transcript Modal ───────────────────────────────────────────────────────

async function openTranscript(videoId, title) {
  currentVideoId = videoId;
  transcriptWithTimestamps = true;
  document.getElementById("transcript-title").textContent = title;
  document.getElementById("toggle-with-ts").classList.add("toggle-btn--active");
  document.getElementById("toggle-without-ts").classList.remove("toggle-btn--active");
  document.getElementById("copy-transcript-btn").classList.add("hidden");
  document.getElementById("transcript-modal").classList.remove("hidden");
  await loadTranscript(true);
}

async function openTranscriptAtSegment(videoId, videoTitle, segmentId, startTime) {
  await openTranscript(videoId, videoTitle);
  const line = Array.from(document.querySelectorAll(".transcript-line")).find(
    (element) => element.dataset.segmentId === segmentId
  );
  if (!line) {
    console.warn("Matched transcript segment was not rendered", { segmentId, startTime });
    showToast("Matched segment is not available in this transcript", "error");
    return;
  }
  line.scrollIntoView({ behavior: "smooth", block: "center" });
  line.classList.add("transcript-line--highlight");
  setTimeout(() => line.classList.remove("transcript-line--highlight"), 2500);
}

async function loadTranscript(withTimestamps) {
  const body = document.getElementById("transcript-body");
  body.innerHTML = '<p class="empty-state">Loading transcript…</p>';

  try {
    if (withTimestamps) {
      const segments = await apiFetch(
        `/api/videos/${currentVideoId}/transcript?with_timestamps=true`
      );
      body.innerHTML = segments
        .map((seg) => {
          const dir = detectDir(seg.text);
          return `
          <div class="transcript-line" data-segment-id="${seg.segment_id}">
            <a href="${seg.youtube_link}" target="_blank" rel="noopener" class="transcript-ts" title="Open on YouTube">[${formatTimestamp(seg.start_time)}]</a>
            <span class="transcript-text" dir="${dir}">${escapeHtml(seg.text)}</span>
          </div>`;
        })
        .join("");
      plainTranscriptText = segments.map((s) => s.text).join(" ");
    } else {
      const data = await apiFetch(
        `/api/videos/${currentVideoId}/transcript?with_timestamps=false`
      );
      plainTranscriptText = data.text;
      const dir = detectDir(data.text);
      body.innerHTML = `<p class="transcript-plain" dir="${dir}">${escapeHtml(data.text)}</p>`;
    }
  } catch (err) {
    body.innerHTML = `<p class="empty-state" style="color:var(--color-error)">${escapeHtml(err.message)}</p>`;
  }
}

function closeModal() {
  document.getElementById("transcript-modal").classList.add("hidden");
  currentVideoId = null;
}

function initModal() {
  document.getElementById("close-modal-btn").addEventListener("click", closeModal);
  document.getElementById("modal-backdrop").addEventListener("click", closeModal);

  document.getElementById("toggle-with-ts").addEventListener("click", async () => {
    transcriptWithTimestamps = true;
    document.getElementById("toggle-with-ts").classList.add("toggle-btn--active");
    document.getElementById("toggle-without-ts").classList.remove("toggle-btn--active");
    document.getElementById("copy-transcript-btn").classList.add("hidden");
    await loadTranscript(true);
  });

  document.getElementById("toggle-without-ts").addEventListener("click", async () => {
    transcriptWithTimestamps = false;
    document.getElementById("toggle-without-ts").classList.add("toggle-btn--active");
    document.getElementById("toggle-with-ts").classList.remove("toggle-btn--active");
    document.getElementById("copy-transcript-btn").classList.remove("hidden");
    await loadTranscript(false);
  });

  document.getElementById("copy-transcript-btn").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(plainTranscriptText);
      showToast("Transcript copied to clipboard");
    } catch (_) {
      showToast("Failed to copy text", "error");
    }
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeModal();
  });
}

// ── Search ─────────────────────────────────────────────────────────────────

async function performSearch(e) {
  e.preventDefault();
  const btn = document.getElementById("search-btn");
  const query = document.getElementById("search-query").value.trim();
  if (!query) {
    showToast("Please enter a search query", "error");
    return;
  }

  setButtonLoading(btn, true);
  const container = document.getElementById("search-results");
  container.innerHTML = "";

  const payload = { query, limit: 20 };
  const channelId = document.getElementById("search-channel").value;
  const dateFrom = document.getElementById("search-date-from").value;
  const dateTo = document.getElementById("search-date-to").value;

  if (channelId) payload.channel_id = channelId;
  if (dateFrom) payload.date_from = new Date(dateFrom).toISOString();
  if (dateTo) payload.date_to = new Date(dateTo + "T23:59:59").toISOString();

  try {
    const results = await apiFetch("/api/search", {
      method: "POST",
      body: JSON.stringify(payload),
    });

    if (!results.length) {
      container.innerHTML = '<p class="empty-state">No matching segments found.</p>';
      return;
    }

    container.innerHTML = results
      .map((r) => {
        const dir = detectDir(r.text);
        const pct = Math.round(r.similarity * 100);
        return `
        <div class="result-card">
          <div class="result-card__header">
            <div>
              <div class="result-card__channel">${escapeHtml(r.channel_name)}</div>
              <div class="result-card__title" dir="${detectDir(r.video_title)}">${escapeHtml(r.video_title)}</div>
            </div>
            <span class="similarity">${pct}% match</span>
          </div>
          <p class="result-card__snippet" dir="${dir}">${escapeHtml(r.text)}</p>
          <div class="result-card__footer">
            <button type="button" class="btn btn--ghost btn--sm" data-video-id="${r.video_id}" data-video-title="${escapeHtml(r.video_title)}" data-segment-id="${r.segment_id}" data-start-time="${r.start_time}" onclick="openTranscriptAtSegment(this.dataset.videoId, this.dataset.videoTitle, this.dataset.segmentId, Number(this.dataset.startTime))">View in transcript</button>
            <span>⏱ ${formatTimestamp(r.start_time)}</span>
            <a href="${r.youtube_link}" target="_blank" rel="noopener" class="btn btn--primary btn--sm">▶ Watch</a>
          </div>
        </div>`;
      })
      .join("");
  } catch (err) {
    showToast(err.message, "error");
  } finally {
    setButtonLoading(btn, false);
  }
}

// ── Add Channel ────────────────────────────────────────────────────────────

function initAddChannelForm() {
  const timeWindow = document.getElementById("time-window");
  const customDates = document.getElementById("custom-dates");
  const customHoursField = document.getElementById("custom-hours-field");

  timeWindow.addEventListener("change", () => {
    customDates.classList.toggle("hidden", timeWindow.value !== "custom");
    customHoursField.classList.toggle(
      "hidden", timeWindow.value !== "custom_hours"
    );
  });

  document.getElementById("add-channel-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = document.getElementById("add-channel-btn");
    setButtonLoading(btn, true);

    const payload = {
      url: document.getElementById("channel-url").value.trim(),
      time_window: timeWindow.value,
    };

    if (timeWindow.value === "custom") {
      const start = document.getElementById("start-date").value;
      const end = document.getElementById("end-date").value;
      if (!start) {
        showToast("Please select a start date", "error");
        setButtonLoading(btn, false);
        return;
      }
      payload.start_date = new Date(start).toISOString();
      if (end) payload.end_date = new Date(end + "T23:59:59").toISOString();
    }
    if (timeWindow.value === "custom_hours") {
      const hours = parseInt(document.getElementById("custom-hours-input").value, 10);
      if (!hours || hours < 1) {
        showToast("Please enter a valid number of hours", "error");
        setButtonLoading(btn, false);
        return;
      }
      payload.custom_hours = hours;
    }

    try {
      await apiFetch("/api/channels", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      showToast("Channel added — processing started");
      document.getElementById("channel-url").value = "";
      startPolling();
      await refreshChannels();
    } catch (err) {
      showToast(err.message, "error");
    } finally {
      setButtonLoading(btn, false);
    }
  });
}

// ── Helpers ────────────────────────────────────────────────────────────────

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

// ── Init ───────────────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", async () => {
  initTheme();
  initAddChannelForm();
  initModal();

  document.getElementById("search-form").addEventListener("submit", performSearch);
  document.getElementById("clear-search-btn").addEventListener("click", () => {
    document.getElementById("search-results").innerHTML = "";
    document.getElementById("search-query").value = "";
    document.getElementById("search-channel").value = "";
    document.getElementById("search-date-from").value = "";
    document.getElementById("search-date-to").value = "";
  });

  document.getElementById("close-videos-btn").addEventListener("click", () => {
    selectedChannelId = null;
    document.getElementById("videos-section").classList.add("hidden");
  });

  const channels = await refreshChannels();
  if (channels && channels.some((c) => !TERMINAL_CHANNEL_STATUSES.has(c.status))) {
    startPolling();
  }
});
