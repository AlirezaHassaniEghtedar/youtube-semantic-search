import { useCallback, useEffect, useState, type ReactElement } from "react";
import { apiFetch } from "../lib/api";
import { useTheme } from "../context/ThemeContext";
import { useChannels } from "../hooks/useChannels";
import { ConversationSidebar } from "./ConversationSidebar";
import { ChatView } from "../views/ChatView";
import { ChannelsView } from "../views/ChannelsView";
import { SearchView } from "../views/SearchView";
import type { ChatConversation } from "../types";
import MingcuteMoonStarsLine from "../icons/MingcuteMoonStarsLine";
import MingcuteSunLine from "../icons/MingcuteSunLine";
import MingcuteYoutubeLine from "../icons/MingcuteYoutubeLine";
import MingcuteMessage4AiLine from "../icons/MingcuteMessage4AiLine";
import MingcutePlayLine from "../icons/MingcutePlayLine";
import MingcuteSearch3Line from "../icons/MingcuteSearch3Line";
import MingcuteArrowsRightLine from "../icons/MingcuteArrowsRightLine";
import MingcuteArrowsLeftLine from "../icons/MingcuteArrowsLeftLine";

type Section = "chat" | "channels" | "search";

// Icon rail — the section switcher. Chat is the default/home view.
const SECTIONS: { id: Section; label: string; icon: ReactElement }[] = [
  {
    id: "chat",
    label: "Chat",
    icon: (
      <MingcuteMessage4AiLine />
    ),
  },
  {
    id: "channels",
    label: "Channels",
    icon: (
      <MingcutePlayLine />
    ),
  },
  {
    id: "search",
    label: "Search",
    icon: (
      <MingcuteSearch3Line />
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
          <MingcuteYoutubeLine />
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
            <MingcuteMoonStarsLine />
          ) : (
            <MingcuteSunLine />
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
            style={{display:"flex" , justifyContent : "center" , alignItems : "center"}}
            title={sidebarCollapsed ? "Show conversations" : "Hide conversations"}
            onClick={() => setSidebarCollapsed((c) => !c)}
          >
            {sidebarCollapsed ? <MingcuteArrowsRightLine /> : <MingcuteArrowsLeftLine />}
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


