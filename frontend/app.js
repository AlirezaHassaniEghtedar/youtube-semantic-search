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

function isChannelActive(c) {
  return !TERMINAL_CHANNEL_STATUSES.has(c.status) || c.done_videos < c.total_videos;
}

function checkStopPolling(channels) {
  if (!channels) return;
  const noneActive = channels.every((c) => !isChannelActive(c));
  if (noneActive) {
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

    const anyActive = channels.some(isChannelActive);
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
  const chatSelect = document.getElementById("chat-channel");
  const current = select.value;
  const currentChat = chatSelect.value;
  
  select.innerHTML = '<option value="">All channels</option>';
  chatSelect.innerHTML = '<option value="">All channels</option>';
  
  channels.forEach((ch) => {
    const opt1 = document.createElement("option");
    opt1.value = ch.id;
    opt1.textContent = ch.name || ch.url;
    select.appendChild(opt1);
    
    const opt2 = document.createElement("option");
    opt2.value = ch.id;
    opt2.textContent = ch.name || ch.url;
    chatSelect.appendChild(opt2);
  });
  
  if (current) select.value = current;
  if (currentChat) chatSelect.value = currentChat;
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
      '<tr><td colspan="6" class="empty-state">No videos in this time window.</td></tr>';
    return;
  }

  tbody.innerHTML = videos
    .map(
      (v) => `
    <tr>
      <td class="video-title" title="${escapeHtml(v.title)}" dir="${detectDir(v.title)}">${escapeHtml(v.title)}</td>
      <td>
        ${v.video_type ? `<span class="badge badge--video-type">${escapeHtml(v.video_type)}</span>` : "—"}
      </td>
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
          const isLocal = seg.source_type === "local";
          const link = isLocal ? seg.media_url : seg.youtube_link;
          const linkTitle = isLocal ? "Play local video" : "Open on YouTube";
          return `
          <div class="transcript-line" data-segment-id="${seg.segment_id}">
            <a href="${link}" target="_blank" rel="noopener" class="transcript-ts" title="${linkTitle}">[${formatTimestamp(seg.start_time)}]</a>
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

function renderSearchResultCard(r) {
  const dir = detectDir(r.text);
  const pct = Math.round(r.similarity * 100);
  const isLocal = r.source_type === "local";
  const watchLink = isLocal ? r.media_url : r.youtube_link;
  const watchLabel = isLocal ? "▶ Play" : "▶ Watch";
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
      <a href="${watchLink}" target="_blank" rel="noopener" class="btn btn--primary btn--sm">${watchLabel}</a>
    </div>
  </div>`;
}

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

    container.innerHTML = results.map(renderSearchResultCard).join("");
  } catch (err) {
    showToast(err.message, "error");
  } finally {
    setButtonLoading(btn, false);
  }
}

// ── Chat ───────────────────────────────────────────────────────────────────

async function sendChatMessage() {
  const input = document.getElementById("chat-input");
  const message = input.value.trim();
  const btn = document.getElementById("chat-send-btn");
  
  if (!message || !currentChatConversationId) return;
  
  input.value = "";
  input.disabled = true;
  setButtonLoading(btn, true);
  
  const messagesContainer = document.getElementById("chat-messages");
  
  try {
    // Show user message
    renderMessage({ role: "user", content: message, created_at: new Date().toISOString() }, messagesContainer);
    messagesContainer.scrollTop = messagesContainer.scrollHeight;
    
    // Show loading indicator
    const loadingEl = document.createElement("div");
    loadingEl.className = "chat-loading";
    loadingEl.innerHTML = '<div class="chat-loading__spinner"></div><span>Thinking…</span>';
    messagesContainer.appendChild(loadingEl);
    messagesContainer.scrollTop = messagesContainer.scrollHeight;
    
    // Send to API
    const payload = { 
      question: message,
      conversation_id: currentChatConversationId 
    };
    const channelId = document.getElementById("chat-channel").value;
    if (channelId) payload.channel_id = channelId;
    
    const response = await apiFetch("/api/chat", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    
    // Remove loading indicator
    loadingEl.remove();
    
    // Show assistant message with sources
    renderMessage({ 
      role: "assistant", 
      content: response.answer, 
      sources: response.sources,
      created_at: new Date().toISOString() 
    }, messagesContainer);
    
    // Update conversation title if it was just set
    if (response.title && currentChatConversationTitle !== response.title) {
      currentChatConversationTitle = response.title;
      await refreshConversationList();
      updateActiveConversationHighlight();
    }
    
    messagesContainer.scrollTop = messagesContainer.scrollHeight;
  } catch (err) {
    loadingEl.remove();
    const errorMsg = document.createElement("div");
    errorMsg.className = "chat-message chat-message--assistant";
    errorMsg.innerHTML = `<div class="chat-message__content" style="color: var(--color-error);"><div class="chat-message__text">Error: ${escapeHtml(err.message)}</div></div>`;
    messagesContainer.appendChild(errorMsg);
    messagesContainer.scrollTop = messagesContainer.scrollHeight;
    showToast(err.message, "error");
  } finally {
    input.disabled = false;
    setButtonLoading(btn, false);
    input.focus();
  }
}

// ── Chat state ──────────────────────────────────────────────────────────────

let currentChatConversationId = null;
let currentChatConversationTitle = null;
let allConversations = [];
let filteredConversations = [];

// ── Markdown rendering ──────────────────────────────────────────────────────

function renderMarkdown(text) {
  // Escape HTML first
  let html = escapeHtml(text);
  
  // Bold: **text** or __text__
  html = html.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
  html = html.replace(/__(.*?)__/g, '<strong>$1</strong>');
  
  // Italic: *text* or _text_
  html = html.replace(/\*(.*?)\*/g, '<em>$1</em>');
  html = html.replace(/_(.*?)_/g, '<em>$1</em>');
  
  // Inline code: `text`
  html = html.replace(/`(.*?)`/g, '<code>$1</code>');
  
  // Convert line breaks and lists
  const lines = html.split('\n');
  let inUl = false;
  let inOl = false;
  let output = [];
  
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim();
    
    // Bullet lists
    if (line.match(/^[-•*] /)) {
      if (!inUl) {
        output.push('<ul>');
        inUl = true;
      }
      output.push('<li>' + line.replace(/^[-•*] /, '') + '</li>');
    }
    // Numbered lists
    else if (line.match(/^\d+\. /)) {
      if (!inOl) {
        output.push('<ol>');
        inOl = true;
      }
      output.push('<li>' + line.replace(/^\d+\. /, '') + '</li>');
    }
    else {
      if (inUl) { output.push('</ul>'); inUl = false; }
      if (inOl) { output.push('</ol>'); inOl = false; }
      if (line) output.push(line);
    }
  }
  
  if (inUl) output.push('</ul>');
  if (inOl) output.push('</ol>');
  
  return output.join('\n');
}

// ── Relative timestamps ──────────────────────────────────────────────────────

function getRelativeTime(isoDate) {
  const date = new Date(isoDate);
  const now = new Date();
  const diffMs = now - date;
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMs / 3600000);
  const diffDays = Math.floor(diffMs / 86400000);
  
  if (diffMins < 1) return "Just now";
  if (diffMins < 60) return `${diffMins}m ago`;
  if (diffHours < 24) return `${diffHours}h ago`;
  if (diffDays < 7) return `${diffDays}d ago`;
  
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function getRecencyGroup(isoDate) {
  const date = new Date(isoDate);
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterday = new Date(today);
  yesterday.setDate(yesterday.getDate() - 1);
  const weekAgo = new Date(today);
  weekAgo.setDate(weekAgo.getDate() - 7);
  
  const dateOnly = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  
  if (dateOnly.getTime() === today.getTime()) return "Today";
  if (dateOnly.getTime() === yesterday.getTime()) return "Yesterday";
  if (dateOnly >= weekAgo) return "Previous 7 days";
  return "Older";
}

// ── Message rendering ──────────────────────────────────────────────────────

function renderMessage(message, container) {
  const msgEl = document.createElement("div");
  msgEl.className = `chat-message chat-message--${message.role}`;
  
  const dir = detectDir(message.content);
  const contentEl = document.createElement("div");
  contentEl.className = "chat-message__content";
  
  const textEl = document.createElement("div");
  textEl.className = "chat-message__text";
  textEl.dir = dir;
  textEl.innerHTML = renderMarkdown(message.content);
  
  contentEl.appendChild(textEl);
  
  if (message.role === "assistant") {
    // Add copy button
    const actionsEl = document.createElement("div");
    actionsEl.className = "chat-message__actions";
    const copyBtn = document.createElement("button");
    copyBtn.className = "chat-message__action";
    copyBtn.textContent = "Copy";
    copyBtn.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(message.content);
        copyBtn.textContent = "✓";
        copyBtn.classList.add("copied");
        setTimeout(() => {
          copyBtn.textContent = "Copy";
          copyBtn.classList.remove("copied");
        }, 1500);
      } catch (e) {
        showToast("Failed to copy", "error");
      }
    });
    actionsEl.appendChild(copyBtn);
    contentEl.appendChild(actionsEl);
  }
  
  msgEl.appendChild(contentEl);
  
  // Add sources if present
  if (message.sources && message.sources.length > 0) {
    const sourcesDiv = document.createElement("div");
    sourcesDiv.className = "chat-message__sources";
    sourcesDiv.innerHTML = message.sources.map(renderSearchResultCard).join("");
    msgEl.appendChild(sourcesDiv);
  }
  
  container.appendChild(msgEl);
}

// ── Conversation management ──────────────────────────────────────────────────

async function createConversation() {
  try {
    const response = await apiFetch("/api/conversations", {
      method: "POST",
    });
    currentChatConversationId = response.id;
    currentChatConversationTitle = response.title;
    await refreshConversationList();
    switchConversation(response.id);
  } catch (err) {
    showToast(err.message, "error");
  }
}

async function refreshConversationList() {
  try {
    const response = await apiFetch("/api/conversations");
    allConversations = response;
    applyConversationFilter();
    updateActiveConversationHighlight();
  } catch (err) {
    showToast(err.message, "error");
  }
}

function applyConversationFilter() {
  const searchInput = document.getElementById("chat-search");
  const query = (searchInput?.value || "").toLowerCase();
  
  if (!query) {
    filteredConversations = allConversations;
  } else {
    filteredConversations = allConversations.filter(c => 
      (c.title || "New chat").toLowerCase().includes(query) ||
      (c.preview || "").toLowerCase().includes(query)
    );
  }
  
  renderConversationList();
}

function groupConversationsByRecency(conversations) {
  const groups = {
    "Today": [],
    "Yesterday": [],
    "Previous 7 days": [],
    "Older": []
  };
  
  for (const conv of conversations) {
    const group = getRecencyGroup(conv.updated_at);
    groups[group].push(conv);
  }
  
  return groups;
}

function renderConversationList() {
  const listEl = document.getElementById("chat-conversation-list");
  const emptyEl = document.getElementById("chat-sidebar-empty");
  
  if (filteredConversations.length === 0) {
    listEl.innerHTML = "";
    emptyEl.classList.remove("hidden");
    return;
  }
  
  emptyEl.classList.add("hidden");
  
  const groups = groupConversationsByRecency(filteredConversations);
  let html = "";
  
  for (const [groupLabel, convs] of Object.entries(groups)) {
    if (convs.length === 0) continue;
    
    html += `<div class="chat-conversation-group">`;
    html += `<div class="chat-conversation-group__label">${escapeHtml(groupLabel)}</div>`;
    
    for (const conv of convs) {
      const isActive = conv.id === currentChatConversationId;
      const title = conv.title || "New chat";
      const preview = (conv.preview || "").substring(0, 60);
      const titleAttr = escapeHtml(title);
      
      html += `<div class="chat-conversation-item ${isActive ? "chat-conversation-item--active" : ""}" data-id="${conv.id}" title="${titleAttr}">`;
      html += `<div class="chat-conversation-item__text">`;
      html += `<div class="chat-conversation-item__title">${escapeHtml(title)}</div>`;
      if (preview) html += `<div class="chat-conversation-item__preview">${escapeHtml(preview)}</div>`;
      html += `</div>`;
      html += `<button class="chat-conversation-item__menu" data-id="${conv.id}">⋮</button>`;
      html += `</div>`;
    }
    
    html += `</div>`;
  }
  
  listEl.innerHTML = html;
  
  // Add event listeners
  listEl.querySelectorAll(".chat-conversation-item").forEach(item => {
    item.addEventListener("click", (e) => {
      if (!e.target.closest(".chat-conversation-item__menu")) {
        switchConversation(item.dataset.id);
      }
    });
  });
  
  // Add menu listeners
  listEl.querySelectorAll(".chat-conversation-item__menu").forEach(btn => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      showConversationMenu(btn, btn.dataset.id);
    });
  });
}

function updateActiveConversationHighlight() {
  document.querySelectorAll(".chat-conversation-item").forEach(item => {
    item.classList.toggle("chat-conversation-item--active", item.dataset.id === currentChatConversationId);
  });
}

async function switchConversation(conversationId) {
  currentChatConversationId = conversationId;
  const conv = allConversations.find(c => c.id === conversationId);
  if (conv) {
    currentChatConversationTitle = conv.title;
  }
  
  updateActiveConversationHighlight();
  
  // Fetch and render messages
  const messagesContainer = document.getElementById("chat-messages");
  const emptyState = document.getElementById("chat-empty-state");
  const activeArea = document.getElementById("chat-active");
  
  try {
    const response = await apiFetch(`/api/conversations/${conversationId}/messages`);
    
    messagesContainer.innerHTML = "";
    
    if (response.length === 0) {
      // Show empty state
      emptyState.classList.remove("hidden");
      activeArea.classList.add("hidden");
    } else {
      // Render messages
      response.forEach(msg => {
        renderMessage(msg, messagesContainer);
      });
      emptyState.classList.add("hidden");
      activeArea.classList.remove("hidden");
      messagesContainer.scrollTop = messagesContainer.scrollHeight;
    }
  } catch (err) {
    showToast(err.message, "error");
  }
}

function showConversationMenu(btn, conversationId) {
  // Remove existing dropdown
  const existing = document.querySelector(".chat-menu-dropdown");
  if (existing) existing.remove();
  
  const dropdown = document.createElement("div");
  dropdown.className = "chat-menu-dropdown";
  
  const renameBtn = document.createElement("button");
  renameBtn.className = "chat-menu-action";
  renameBtn.textContent = "Rename";
  renameBtn.addEventListener("click", () => {
    dropdown.remove();
    renameConversation(conversationId);
  });
  
  const deleteBtn = document.createElement("button");
  deleteBtn.className = "chat-menu-action chat-menu-action--danger";
  deleteBtn.textContent = "Delete";
  deleteBtn.addEventListener("click", () => {
    dropdown.remove();
    deleteConversation(conversationId);
  });
  
  dropdown.appendChild(renameBtn);
  dropdown.appendChild(deleteBtn);
  btn.parentElement.appendChild(dropdown);
  
  // Close when clicking elsewhere
  setTimeout(() => {
    document.addEventListener("click", function closeDropdown(e) {
      if (!dropdown.contains(e.target) && e.target !== btn) {
        dropdown.remove();
        document.removeEventListener("click", closeDropdown);
      }
    });
  }, 0);
}

async function renameConversation(conversationId) {
  const conv = allConversations.find(c => c.id === conversationId);
  const newTitle = prompt("New title:", conv.title || "");
  
  if (newTitle !== null && newTitle !== conv.title) {
    try {
      await apiFetch(`/api/conversations/${conversationId}`, {
        method: "PATCH",
        body: JSON.stringify({ title: newTitle.trim() || null }),
      });
      await refreshConversationList();
    } catch (err) {
      showToast(err.message, "error");
    }
  }
}

async function deleteConversation(conversationId) {
  if (!confirm("Delete this conversation?")) return;
  
  try {
    await apiFetch(`/api/conversations/${conversationId}`, {
      method: "DELETE",
    });
    
    // If deleted conversation was active, switch to another
    if (conversationId === currentChatConversationId) {
      if (allConversations.length > 1) {
        const nextConv = allConversations.find(c => c.id !== conversationId);
        if (nextConv) {
          await switchConversation(nextConv.id);
        }
      } else {
        currentChatConversationId = null;
        currentChatConversationTitle = null;
        document.getElementById("chat-empty-state").classList.remove("hidden");
        document.getElementById("chat-active").classList.add("hidden");
      }
    }
    
    await refreshConversationList();
  } catch (err) {
    showToast(err.message, "error");
  }
}

function initChat() {
  // New chat button
  document.getElementById("new-chat-btn").addEventListener("click", createConversation);
  
  // Search filter
  document.getElementById("chat-search").addEventListener("input", applyConversationFilter);
  
  // Send message
  const sendBtn = document.getElementById("chat-send-btn");
  const input = document.getElementById("chat-input");
  
  sendBtn.addEventListener("click", () => {
    if (currentChatConversationId) {
      sendChatMessage();
    }
  });
  
  input.addEventListener("keypress", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (currentChatConversationId) {
        sendChatMessage();
      }
    }
  });
  
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && e.shiftKey) {
      // Allow shift+enter for newline
    }
  });
  
  // Load initial conversations
  refreshConversationList();
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

// ── Add Local Video ──────────────────────────────────────────────────────

function initAddLocalVideoForm() {
  const videoInput = document.getElementById("local-video-path");
  const subtitleInput = document.getElementById("local-subtitle-path");

  const hasNativeDialogs = typeof window.pywebview !== "undefined";
  document.getElementById("browse-video-btn").classList.toggle("hidden", !hasNativeDialogs);
  document.getElementById("browse-subtitle-btn").classList.toggle("hidden", !hasNativeDialogs);

  if (hasNativeDialogs) {
    document.getElementById("browse-video-btn").addEventListener("click", async () => {
      const path = await window.pywebview.api.select_video_file();
      if (path) videoInput.value = path;
    });
    document.getElementById("browse-subtitle-btn").addEventListener("click", async () => {
      const path = await window.pywebview.api.select_subtitle_file();
      if (path) subtitleInput.value = path;
    });
  }

  document.getElementById("add-local-video-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = document.getElementById("add-local-video-btn");
    const videoPath = videoInput.value.trim();
    const subtitlePath = subtitleInput.value.trim();

    if (!videoPath) {
      showToast("Please choose a video file", "error");
      return;
    }

    setButtonLoading(btn, true);
    try {
      await apiFetch("/api/local-videos", {
        method: "POST",
        body: JSON.stringify({
          video_path: videoPath,
          subtitle_path: subtitlePath || null,
          title: document.getElementById("local-video-title").value.trim() || null,
        }),
      });
      showToast(
        subtitlePath
          ? "Local video added — indexing in the background"
          : "Local video added — looking for subtitles, or transcribing with Whisper in the background"
      );
      videoInput.value = "";
      subtitleInput.value = "";
      document.getElementById("local-video-title").value = "";
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
  initAddLocalVideoForm();
  initModal();
  initChat();

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
