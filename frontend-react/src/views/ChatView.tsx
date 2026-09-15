import {
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";
import { apiFetch } from "../lib/api";
import { detectDir } from "../lib/format";
import { renderMarkdown } from "../lib/markdown";
import { useToast } from "../context/ToastContext";
import { ResultCard } from "../components/ResultCard";
import { Button } from "../components/Button";
import type {
  Channel,
  ChatMessage,
  SearchResult,
} from "../types";

// Chat view — the primary, home experience. The conversation list lives in the
// persistent app sidebar (AppShell); this component renders the conversation
// pane. Both empty states are preserved from the vanilla app:
//  - no conversation selected  -> full placeholder (no input)
//  - conversation with 0 msgs  -> placeholder + still-visible, usable input
// After sending, AppShell refreshes the sidebar via the conversations-changed
// window event so the auto-generated title shows up live.

interface ChatViewProps {
  activeId: string | null;
  channels: Channel[];
}

export function ChatView({ activeId, channels }: ChatViewProps) {
  const { showToast } = useToast();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [chatChannelId, setChatChannelId] = useState("");
  const [loadedOnce, setLoadedOnce] = useState(false);
  // Currently-edited user message id (inline edit state).
  const [editingId, setEditingId] = useState<string | null>(null);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Auto-grow the textarea to fit its content: reset to "auto", read the
  // natural scrollHeight, then pin the height to it. The CSS max-height
  // (.chat-textarea { max-height: 120px }) caps the growth; beyond the cap
  // overflow-y kicks in and the textarea scrolls internally instead.
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [input]);

  // Load messages for the active conversation
  useEffect(() => {
    if (!activeId) {
      setMessages([]);
      setLoadedOnce(false);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const data = await apiFetch<ChatMessage[]>(
          `/api/conversations/${activeId}/messages`
        );
        if (cancelled) return;
        setMessages(data);
        setLoadedOnce(true);
      } catch (err) {
        if (!cancelled) showToast((err as Error).message, "error");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [activeId, showToast]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "auto" });
  }, [messages, sending]);

  async function sendQuestion(text: string, replaceFromIndex: number | null) {
    const trimmed = text.trim();
    if (!trimmed || !activeId || sending) return;

    const optimistic: ChatMessage = {
      id: `temp-${Date.now()}`,
      conversation_id: activeId,
      role: "user",
      content: trimmed,
      sources: null,
      created_at: new Date().toISOString(),
    };
    // Normal send: append. Edit send: the edited message and everything after
    // it have been truncated (server-side) — replace from the edited slot.
    setMessages((prev) =>
      replaceFromIndex === null
        ? [...prev, optimistic]
        : [...prev.slice(0, replaceFromIndex), optimistic]
    );
    setInput("");
    setSending(true);

    const payload: Record<string, unknown> = {
      question: trimmed,
      conversation_id: activeId,
    };
    if (chatChannelId) payload.channel_id = chatChannelId;

    try {
      const response = await apiFetch<{
        answer: string;
        sources: SearchResult[] | null;
        title: string | null;
      }>("/api/chat", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      setMessages((prev) => [
        ...prev,
        {
          id: `temp-a-${Date.now()}`,
          conversation_id: activeId,
          role: "assistant",
          content: response.answer,
          sources: response.sources,
          created_at: new Date().toISOString(),
        },
      ]);
      // Title may have been auto-generated — tell the sidebar to refresh live
      window.dispatchEvent(new CustomEvent("conversations-changed"));
      // Re-sync from the server so the optimistic temp- ids are replaced by
      // the real persisted ones — actions that address a message by id
      // (copy sources links, edit-truncate) need the server id to work.
      const fresh = await apiFetch<ChatMessage[]>(
        `/api/conversations/${activeId}/messages`
      );
      setMessages(fresh);
    } catch (err) {
      const msg = (err as Error).message;
      setMessages((prev) => [
        ...prev,
        {
          id: `temp-e-${Date.now()}`,
          conversation_id: activeId,
          role: "assistant",
          content: `Error: ${msg}`,
          sources: null,
          created_at: new Date().toISOString(),
        },
      ]);
      showToast(msg, "error");
    } finally {
      setSending(false);
    }
  }

  async function handleSend() {
    await sendQuestion(input, null);
  }

  async function handleSaveEdit(message: ChatMessage, newText: string) {
    const trimmed = newText.trim();
    if (!trimmed || !activeId) return;
    const index = messages.findIndex((m) => m.id === message.id);
    if (index === -1) return;
    try {
      // Discard the edited message and everything after it on the SERVER, so
      // the truncation survives a restart, then re-send the edited question.
      await apiFetch(`/api/conversations/${activeId}/truncate`, {
        method: "POST",
        body: JSON.stringify({ after_message_id: message.id }),
      });
      setEditingId(null);
      await sendQuestion(trimmed, index);
    } catch (err) {
      showToast((err as Error).message, "error");
    }
  }

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void handleSend();
    }
    // Shift+Enter falls through: newline
  }

  return (
    <div className="chat-main">
      {!activeId ? (
        <div className="chat-empty-state">
          <div className="chat-empty-state__inner">
            <div className="chat-empty-state__icon">💬</div>
            <h2>What can I help you find?</h2>
            <p>Select a conversation or create a new one to start chatting.</p>
          </div>
        </div>
      ) : (
        <div className="chat-active">
          <div className="chat-messages">
            {loadedOnce && messages.length === 0 ? (
              <div className="chat-empty-conversation">
                <p>Ask anything about your synced videos.</p>
              </div>
            ) : (
              messages.map((m) => (
                <ChatMessageItem
                  key={m.id}
                  message={m}
                  isEditing={editingId === m.id}
                  onStartEdit={() => setEditingId(m.id)}
                  onCancelEdit={() => setEditingId(null)}
                  onSaveEdit={(text) => void handleSaveEdit(m, text)}
                  disabled={sending}
                />
              ))
            )}
            {sending && (
              <div className="chat-loading">
                <div className="chat-loading__spinner" />
                <span>Thinking…</span>
              </div>
            )}
            <div ref={messagesEndRef} />
          </div>
          <div className="chat-input-area">
            <select
              className="form__select"
              value={chatChannelId}
              onChange={(e) => setChatChannelId(e.target.value)}
              aria-label="Scope chat to a channel"
            >
              <option value="">All channels</option>
              {channels.map((ch) => (
                <option key={ch.id} value={ch.id}>
                  {ch.name || ch.url}
                </option>
              ))}
            </select>
            <textarea
              ref={textareaRef}
              className="chat-textarea"
              placeholder="Ask a question about your videos…"
              dir="auto"
              rows={1}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
            />
            <Button onClick={() => void handleSend()} loading={sending}>
              Send
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Message item ────────────────────────────────────────────────────────────

interface ChatMessageItemProps {
  message: ChatMessage;
  isEditing: boolean;
  onStartEdit: () => void;
  onCancelEdit: () => void;
  onSaveEdit: (text: string) => void;
  disabled: boolean;
}

function ChatMessageItem({
  message,
  isEditing,
  onStartEdit,
  onCancelEdit,
  onSaveEdit,
  disabled,
}: ChatMessageItemProps) {
  const { showToast } = useToast();
  const [copied, setCopied] = useState(false);
  const [editText, setEditText] = useState(message.content);
  const editInputRef = useRef<HTMLTextAreaElement>(null);
  const dir = detectDir(message.content);

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(message.content);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      showToast("Failed to copy", "error");
    }
  }

  // Pre-fill and focus the inline editor when edit mode opens. Same prompt →
  // inline field with save/cancel pattern as the conversation rename flow.
  useEffect(() => {
    if (isEditing) {
      setEditText(message.content);
      // Wait one frame so the textarea is mounted before focusing/selecting.
      requestAnimationFrame(() => {
        editInputRef.current?.focus();
        editInputRef.current?.select();
      });
    }
  }, [isEditing, message.content]);

  function handleEditKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      onSaveEdit(editText);
    }
    if (e.key === "Escape") {
      e.preventDefault();
      onCancelEdit();
    }
  }

  return (
    <div className={`chat-message chat-message--${message.role}`}>
      <div className="chat-message__content">
        {isEditing ? (
          <div className="chat-message__edit">
            <textarea
              ref={editInputRef}
              className="chat-textarea chat-message__edit-input"
              dir={dir}
              rows={2}
              value={editText}
              onChange={(e) => setEditText(e.target.value)}
              onKeyDown={handleEditKeyDown}
              disabled={disabled}
            />
            <div className="chat-message__edit-actions">
              <Button size="sm" onClick={() => onSaveEdit(editText)} disabled={disabled}>
                Save &amp; resend
              </Button>
              <Button variant="ghost" size="sm" onClick={onCancelEdit} disabled={disabled}>
                Cancel
              </Button>
            </div>
          </div>
        ) : (
          <div
            className="chat-message__text"
            dir={dir}
            dangerouslySetInnerHTML={{ __html: renderMarkdown(message.content) }}
          />
        )}
        {!isEditing && (
          <div className="chat-message__actions">
            <button
              type="button"
              className={`chat-message__action ${copied ? "copied" : ""}`}
              onClick={handleCopy}
            >
              {copied ? "✓" : "Copy"}
            </button>
            {message.role === "user" && (
              <button
                type="button"
                className="chat-message__action"
                onClick={onStartEdit}
                disabled={disabled}
              >
                Edit
              </button>
            )}
          </div>
        )}
      </div>
      {message.sources && message.sources.length > 0 && (
        <div className="chat-message__sources">
          {message.sources.map((s) => (
            <ResultCard key={s.segment_id} result={s} />
          ))}
        </div>
      )}
    </div>
  );
}
