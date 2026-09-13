import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch } from "../lib/api";
import type { Channel } from "../types";

const TERMINAL_CHANNEL_STATUSES = new Set(["done", "error", "stopped"]);
const POLL_INTERVAL_MS = 3000;

export function isChannelActive(c: Channel): boolean {
  return !TERMINAL_CHANNEL_STATUSES.has(c.status) || c.done_videos < c.total_videos;
}

// Replaces the vanilla global pollInterval with a hook-owned interval that is
// always cleaned up on unmount — no leaked timers, no duplicated loops across
// re-renders. Polling only runs while at least one channel is active, matching
// startPolling/checkStopPolling semantics.
export function useChannels() {
  const [channels, setChannels] = useState<Channel[]>([]);
  const [error, setError] = useState<string | null>(null);
  const channelsRef = useRef<Channel[]>([]);

  const refresh = useCallback(async (): Promise<Channel[]> => {
    try {
      const data = await apiFetch<Channel[]>("/api/channels");
      channelsRef.current = data;
      setChannels(data);
      setError(null);
      return data;
    } catch (err) {
      console.error("Failed to load channels:", err);
      setError((err as Error).message);
      return channelsRef.current;
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const anyActive = channels.some(isChannelActive);

  useEffect(() => {
    if (!anyActive) return;
    const interval = setInterval(async () => {
      await refresh();
    }, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [anyActive, refresh]);

  return { channels, anyActive, error, refresh };
}
