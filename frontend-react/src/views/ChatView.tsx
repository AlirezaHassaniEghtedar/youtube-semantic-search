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

  const messagesEndRef = useRef<HTMLDivElement>(null);

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

  async function handleSend() {
    const trimmed = input.trim();
    if (!trimmed || !activeId || sending) return;

    const optimistic: ChatMessage = {
      id: `temp-${Date.now()}`,
      conversation_id: activeId,
      role: "user",
      content: trimmed,
      sources: null,
      created_at: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, optimistic]);
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
              messages.map((m) => <ChatMessageItem key={m.id} message={m} />)
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

function ChatMessageItem({ message }: { message: ChatMessage }) {
  const { showToast } = useToast();
  const [copied, setCopied] = useState(false);
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

  return (
    <div className={`chat-message chat-message--${message.role}`}>
      <div className="chat-message__content">
        <div
          className="chat-message__text"
          dir={dir}
          dangerouslySetInnerHTML={{ __html: renderMarkdown(message.content) }}
        />
        {message.role === "assistant" && (
          <div className="chat-message__actions">
            <button
              type="button"
              className={`chat-message__action ${copied ? "copied" : ""}`}
              onClick={handleCopy}
            >
              {copied ? "✓" : "Copy"}
            </button>
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
