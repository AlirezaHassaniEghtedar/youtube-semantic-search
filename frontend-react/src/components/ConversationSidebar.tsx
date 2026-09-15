import { useEffect, useRef, useState } from "react";
import { apiFetch } from "../lib/api";
import { getRecencyGroup } from "../lib/format";
import { useToast } from "../context/ToastContext";
import { Button } from "../components/Button";
import type { ChatConversation } from "../types";
import MingcuteEditLine from "../icons/MingcuteEditLine";

// Conversation sidebar — port of the vanilla chat sidebar plus the grouping
// from groupConversationsByRecency. The active-item hover contrast bug from
// the vanilla CSS (duplicate --active:hover rules) is intentionally NOT
// carried over: hover on the active item now brightens it slightly instead
// of fighting itself.

interface ConversationSidebarProps {
  conversations: ChatConversation[];
  activeId: string | null;
  onSwitch: (id: string) => void;
  onCreated: (conversation: ChatConversation) => void;
  onChanged: () => void;
  collapsed: boolean;
}

const GROUP_ORDER = ["Today", "Yesterday", "Previous 7 days", "Older"] as const;

export function ConversationSidebar({
  conversations,
  activeId,
  onSwitch,
  onCreated,
  onChanged,
  collapsed,
}: ConversationSidebarProps) {
  const { showToast } = useToast();
  const [filter, setFilter] = useState("");
  const [menuFor, setMenuFor] = useState<string | null>(null);
  const sidebarRef = useRef<HTMLElement>(null);

  // Close the ⋮ menu when clicking anywhere else
  useEffect(() => {
    if (!menuFor) return;
    const handler = (e: MouseEvent) => {
      const target = e.target as HTMLElement;
      if (!target.closest(".chat-menu-dropdown") && !target.closest(".chat-conversation-item__menu")) {
        setMenuFor(null);
      }
    };
    document.addEventListener("click", handler);
    return () => document.removeEventListener("click", handler);
  }, [menuFor]);

  async function handleNewChat() {
    try {
      const response = await apiFetch<ChatConversation>("/api/conversations", {
        method: "POST",
        body: JSON.stringify({}),
      });
      // Add the new conversation to the list IMMEDIATELY (before the refresh
      // resolves) so the active-id fallback doesn't see it missing and reset
      // the selection to an older conversation.
      onCreated(response);
      onChanged();
    } catch (err) {
      showToast((err as Error).message, "error");
    }
  }

  async function handleRename(id: string) {
    const conv = conversations.find((c) => c.id === id);
    const newTitle = window.prompt("New title:", conv?.title || "");
    if (newTitle === null) return;
    if (newTitle.trim() === (conv?.title ?? "")) return;
    try {
      await apiFetch(`/api/conversations/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ title: newTitle.trim() || null }),
      });
      onChanged();
    } catch (err) {
      showToast((err as Error).message, "error");
    }
  }

  async function handleDelete(id: string) {
    if (!window.confirm("Delete this conversation?")) return;
    try {
      await apiFetch(`/api/conversations/${id}`, { method: "DELETE" });
      onChanged();
    } catch (err) {
      showToast((err as Error).message, "error");
    }
  }

  // Filter + group (applyConversationFilter + groupConversationsByRecency)
  const visible = filter.trim()
    ? conversations.filter(
        (c) =>
          (c.title || "New chat").toLowerCase().includes(filter.toLowerCase()) ||
          (c.preview || "").toLowerCase().includes(filter.toLowerCase())
      )
    : conversations;

  const groups: Record<string, ChatConversation[]> = {
    Today: [],
    Yesterday: [],
    "Previous 7 days": [],
    Older: [],
  };
  for (const conv of visible) {
    groups[getRecencyGroup(conv.updated_at)].push(conv);
  }

  if (collapsed) return null;

  return (
    <aside className="chat-sidebar" ref={sidebarRef}>
      <Button variant="primary" block onClick={handleNewChat}>
        <MingcuteEditLine />
        <span style={{marginLeft:"0.5rem"}}>New Chat</span>
      </Button>

      <input
        type="text"
        className="chat-search"
        placeholder="Search conversations…"
        dir="auto"
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
      />

      <div className="chat-conversation-list">
        {visible.length === 0 ? (
          <div className="chat-sidebar-empty">
            <p>
              No conversations yet.
              <br />
              Start one with the button above.
            </p>
          </div>
        ) : (
          GROUP_ORDER.map((label) =>
            groups[label].length === 0 ? null : (
              <div key={label} className="chat-conversation-group">
                <div className="chat-conversation-group__label">{label}</div>
                {groups[label].map((conv) => {
                  const isActive = conv.id === activeId;
                  const title = conv.title || "New chat";
                  const preview = (conv.preview || "").substring(0, 60);
                  return (
                    <div
                      key={conv.id}
                      className={`chat-conversation-item ${isActive ? "chat-conversation-item--active" : ""}`}
                      title={title}
                      onClick={() => onSwitch(conv.id)}
                    >
                      <div className="chat-conversation-item__text">
                        <div className="chat-conversation-item__title">{title}</div>
                        {preview && (
                          <div className="chat-conversation-item__preview">{preview}</div>
                        )}
                      </div>
                      <button
                        type="button"
                        className="chat-conversation-item__menu"
                        onClick={(e) => {
                          e.stopPropagation();
                          setMenuFor(menuFor === conv.id ? null : conv.id);
                        }}
                      >
                        ⋮
                      </button>
                      {menuFor === conv.id && (
                        <div className="chat-menu-dropdown" onClick={(e) => e.stopPropagation()}>
                          <button
                            type="button"
                            className="chat-menu-action"
                            onClick={() => {
                              setMenuFor(null);
                              void handleRename(conv.id);
                            }}
                          >
                            Rename
                          </button>
                          <button
                            type="button"
                            className="chat-menu-action chat-menu-action--danger"
                            onClick={() => {
                              setMenuFor(null);
                              void handleDelete(conv.id);
                            }}
                          >
                            Delete
                          </button>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )
          )
        )}
      </div>
    </aside>
  );
}
