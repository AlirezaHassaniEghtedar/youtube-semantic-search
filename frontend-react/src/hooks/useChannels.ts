import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch } from "../lib/api";
import type { Channel } from "../types";

const TERMINAL_CHANNEL_STATUSES = new Set(["done", "error", "stopped"]);
const POLL_INTERVAL_MS = 3000;

// A channel is "active" (shows "Syncing…", keeps polling) only while its own
// status says a sync job is actually running. The channel status is the
// authoritative signal: run_channel_pipeline() always drives it to a terminal
// value (DONE/ERROR/STOPPED) when the job finishes, no matter how the job ended.
//
// The old `!terminal || done_videos < total_videos` was wrong because
// total_videos counts ALL videos, including VideoStatus.UPCOMING_EVENT rows —
// those are intentionally never processed to DONE until their scheduled time
// passes and a future sync resets them to PENDING. For any channel with an
// upcoming event, done_videos could never catch up to total_videos, so the
// count comparison permanently overrode a genuinely terminal status and the
// indicator showed "Syncing…" forever. Do not derive activity from raw video
// counts: in-progress video states are transient and always resolved (or
// deliberately parked as UPCOMING_EVENT) before the status goes terminal.

export function isChannelActive(c: Channel): boolean {
  if (TERMINAL_CHANNEL_STATUSES.has(c.status)) return false;
  return c.done_videos < c.total_videos;
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
