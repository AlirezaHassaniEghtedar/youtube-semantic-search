import { useCallback, useEffect, useState, type ReactElement } from "react";
import { apiFetch } from "../lib/api";
import { useTheme } from "../context/ThemeContext";
import { useChannels } from "../hooks/useChannels";
import { ConversationSidebar } from "./ConversationSidebar";
import { ChatView } from "../views/ChatView";
import { ChannelsView } from "../views/ChannelsView";
import { SearchView } from "../views/SearchView";
import type { ChatConversation } from "../types";

type Section = "chat" | "channels" | "search";

// Icon rail — the section switcher. Chat is the default/home view.
const SECTIONS: { id: Section; label: string; icon: ReactElement }[] = [
  {
    id: "chat",
    label: "Chat",
    icon: (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2z" />
      </svg>
    ),
  },
  {
    id: "channels",
    label: "Channels",
    icon: (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M21 3H3c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h18c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm-9 14l-7-5 7-5v10z" />
      </svg>
    ),
  },
  {
    id: "search",
    label: "Search",
    icon: (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M15.5 14h-.79l-.28-.27a6.5 6.5 0 1 0-.7.7l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0A4.5 4.5 0 1 1 14 9.5 4.5 4.5 0 0 1 9.5 14z" />
      </svg>
    ),
  },
];

export function AppShell() {
  const { theme, toggleTheme } = useTheme();
  const { channels } = useChannels();

  const [section, setSection] = useState<Section>("chat");
  // On narrow viewports the sidebar overlays the content — start collapsed.
  // On desktop widths (the app targets >= 1000px) it starts visible.
  const [sidebarCollapsed, setSidebarCollapsed] = useState(
    () =>
      typeof window !== "undefined" &&
      window.matchMedia("(max-width: 640px)").matches
  );
  const [isNarrow, setIsNarrow] = useState(
    () =>
      typeof window !== "undefined" &&
      window.matchMedia("(max-width: 640px)").matches
  );

  useEffect(() => {
    const mq = window.matchMedia("(max-width: 640px)");
    const handler = (e: MediaQueryListEvent) => {
      setIsNarrow(e.matches);
      if (e.matches) setSidebarCollapsed(true);
    };
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, []);
  const [conversations, setConversations] = useState<ChatConversation[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);

  const refreshConversations = useCallback(async () => {
    try {
      const data = await apiFetch<ChatConversation[]>("/api/conversations");
      setConversations(data);
      return data;
    } catch (err) {
      console.error("Failed to load conversations:", err);
      return [];
    }
  }, []);

  useEffect(() => {
    void refreshConversations();
    const handler = () => void refreshConversations();
    window.addEventListener("conversations-changed", handler);
    return () => window.removeEventListener("conversations-changed", handler);
  }, [refreshConversations]);

  // If the active conversation disappears (deleted), fall back to another
  useEffect(() => {
    if (
      activeConversationId &&
      conversations.length > 0 &&
      !conversations.some((c) => c.id === activeConversationId)
    ) {
      setActiveConversationId(conversations[0].id);
    }
    if (conversations.length === 0) {
      setActiveConversationId(null);
    }
  }, [conversations, activeConversationId]);

  return (
    <div className="app-shell">
      <nav className="icon-rail" aria-label="Sections">
        <div className="icon-rail__brand" title="YouTube Semantic Search">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M21 3H3c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h18c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm-9 14l-7-5 7-5v10z" />
          </svg>
        </div>
        {SECTIONS.map((s) => (
          <button
            key={s.id}
            type="button"
            className={`icon-rail__btn ${section === s.id ? "icon-rail__btn--active" : ""}`}
            title={s.label}
            aria-label={s.label}
            onClick={() => setSection(s.id)}
          >
            {s.icon}
          </button>
        ))}
        <div className="icon-rail__spacer" />
        <button
          type="button"
          className="icon-rail__btn"
          title={theme === "light" ? "Switch to dark" : "Switch to light"}
          aria-label="Toggle theme"
          onClick={toggleTheme}
        >
          {theme === "light" ? (
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M12 7a5 5 0 1 0 0 10 5 5 0 0 0 0-10zM2 13h2a1 1 0 0 0 0-2H2a1 1 0 0 0 0 2zm18 0h2a1 1 0 0 0 0-2h-2a1 1 0 0 0 0 2zM11 2v2a1 1 0 0 0 2 0V2a1 1 0 0 0-2 0zm0 18v2a1 1 0 0 0 2 0v-2a1 1 0 0 0-2 0zM5.99 4.58a1 1 0 0 0-1.41 1.41l1.06 1.06a1 1 0 0 0 1.41-1.41L5.99 4.58zm12.37 12.37a1 1 0 0 0-1.41 1.41l1.06 1.06a1 1 0 0 0 1.41-1.41l-1.06-1.06zm1.06-10.96a1 1 0 0 0-1.41-1.41l-1.06 1.06a1 1 0 0 0 1.41 1.41l1.06-1.06zM7.05 18.36a1 1 0 0 0-1.41-1.41l-1.06 1.06a1 1 0 0 0 1.41 1.41l1.06-1.06z" />
            </svg>
          ) : (
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M12.3 2.02a9.9 9.9 0 0 1 5.28 1.55 10 10 0 1 0 8.4 15.05A10 10 0 1 1 12.3 2.02z" />
            </svg>
          )}
        </button>
      </nav>

      {section === "chat" && !sidebarCollapsed && isNarrow && (
        <div
          className="sidebar-backdrop"
          onClick={() => setSidebarCollapsed(true)}
        />
      )}

      {section === "chat" ? (
        <ConversationSidebar
          conversations={conversations}
          activeId={activeConversationId}
          onSwitch={(id) => {
            setActiveConversationId(id);
            if (isNarrow) setSidebarCollapsed(true);
          }}
          onCreated={(conv) => {
            // Prepend the fresh conversation right away so it exists in the
            // list before the async refresh lands — otherwise the fallback
            // effect can clobber the selection mid-flight.
            setConversations((prev) => {
              if (prev.some((c) => c.id === conv.id)) return prev;
              return [conv, ...prev];
            });
            setActiveConversationId(conv.id);
          }}
          onChanged={() => void refreshConversations()}
          collapsed={sidebarCollapsed}
        />
      ) : null}

      <main className={`app-content ${section === "chat" ? "app-content--chat" : ""}`}>
        {section === "chat" && (
          <button
            type="button"
            className="sidebar-toggle"
            title={sidebarCollapsed ? "Show conversations" : "Hide conversations"}
            onClick={() => setSidebarCollapsed((c) => !c)}
          >
            {sidebarCollapsed ? "»" : "«"}
          </button>
        )}
        {section === "chat" ? (
          <ChatView activeId={activeConversationId} channels={channels} />
        ) : section === "channels" ? (
          <ChannelsView />
        ) : (
          <SearchView channels={channels} />
        )}
      </main>
    </div>
  );
}


