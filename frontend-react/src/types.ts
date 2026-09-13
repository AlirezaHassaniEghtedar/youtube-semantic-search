// Types mirroring app/schemas.py and the raw dict responses from the routers.

export interface Channel {
  id: string;
  url: string;
  name: string;
  status: string;
  last_synced_at: string | null;
  created_at: string;
  total_videos: number;
  done_videos: number;
}

export interface SyncJob {
  id: string;
  time_window: string;
  requested_max_items: number | null;
  status: string;
  new_videos_found: number;
  error_message: string | null;
  created_at: string;
  finished_at: string | null;
}

export interface Video {
  id: string;
  channel_id: string;
  youtube_video_id: string;
  title: string;
  published_at: string | null;
  duration_seconds: number | null;
  live_status: string | null;
  scheduled_start_at: string | null;
  video_type: string | null;
  status: string;
  error_message: string | null;
}

export interface TranscriptSegment {
  segment_id: string;
  start_time: number;
  end_time: number;
  text: string;
  source_type: string;
  youtube_link: string | null;
  media_url: string | null;
}

export interface SearchResult {
  segment_id: string;
  video_id: string;
  youtube_video_id: string;
  video_title: string;
  channel_name: string;
  start_time: number;
  end_time: number;
  text: string;
  similarity: number;
  source_type: string;
  youtube_link: string | null;
  media_url: string | null;
}

export interface ChatConversation {
  id: string;
  title: string | null;
  preview: string;
  created_at: string;
  updated_at: string;
}

export interface ChatMessage {
  id: string;
  conversation_id: string;
  role: "user" | "assistant" | string;
  content: string;
  sources: SearchResult[] | null;
  created_at: string;
}

export interface ChatResponse {
  answer: string;
  sources: SearchResult[];
  title: string | null;
}

export interface AddChannelPayload {
  url: string;
  time_window: string;
  start_date?: string;
  end_date?: string;
  custom_hours?: number;
}

export interface SearchPayload {
  query: string;
  limit: number;
  channel_id?: string;
  date_from?: string;
  date_to?: string;
}

export interface ChatSendPayload {
  question: string;
  conversation_id: string;
  channel_id?: string;
}

export interface LocalVideoPayload {
  video_path: string;
  subtitle_path: string | null;
  title: string | null;
}
