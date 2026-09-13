// pywebview bridge types. run_app.py exposes an Api instance as
// window.pywebview.api with native file-picker methods. These are Python-side
// OS integrations — preserved exactly as-is, never reimplemented in the browser.

interface PyWebviewApi {
  select_video_file(): Promise<string | null>;
  select_subtitle_file(): Promise<string | null>;
}

interface PyWebview {
  api: PyWebviewApi;
}

declare global {
  interface Window {
    pywebview?: PyWebview;
  }
}

export function hasNativeDialogs(): boolean {
  return typeof window !== "undefined" && !!window.pywebview;
}

export async function selectVideoFile(): Promise<string | null> {
  if (!window.pywebview) return null;
  return window.pywebview.api.select_video_file();
}

export async function selectSubtitleFile(): Promise<string | null> {
  if (!window.pywebview) return null;
  return window.pywebview.api.select_subtitle_file();
}
