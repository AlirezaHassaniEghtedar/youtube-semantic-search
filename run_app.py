"""Launch YouTube Semantic Search as a native Windows desktop window."""

import logging
import os
import sys
import threading
import time
from pathlib import Path

import httpx
import uvicorn
import webview

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

HOST = "127.0.0.1"
PORT = 8000
HEALTH_URL = f"http://{HOST}:{PORT}/api/health"
MAX_WAIT_SECONDS = 60
POLL_INTERVAL = 0.5

project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(project_root))

class Api:
    """Exposed to the frontend as window.pywebview.api.* so the UI can open
    native file-picker dialogs and get real filesystem paths back — a
    browser <input type="file"> cannot return an absolute path, but the
    backend (running on this same machine) needs one to read the video and
    subtitle files directly without copying them.
    """

    def select_video_file(self) -> str | None:
        return self._select_file(
            "Choose a video file",
            file_types=(
                "Video files (*.mp4;*.mkv;*.webm;*.avi;*.mov;*.m4v;*.flv)",
                "All files (*.*)",
            ),
        )

    def select_subtitle_file(self) -> str | None:
        return self._select_file(
            "Choose a subtitle file",
            file_types=("Subtitle files (*.srt;*.vtt)", "All files (*.*)"),
        )

    @staticmethod
    def _select_file(dialog_title: str, file_types: tuple[str, ...]) -> str | None:
        window = webview.windows[0] if webview.windows else None
        if window is None:
            return None
        result = window.create_file_dialog(
            webview.OPEN_DIALOG,
            allow_multiple=False,
            file_types=file_types,
        )
        if not result:
            return None
        return result[0]


class ServerController:
    def __init__(self) -> None:
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        config = uvicorn.Config(
            "app.main:app",
            host=HOST,
            port=PORT,
            log_level="info",
            access_log=False,
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(
            target=self._server.run,
            daemon=True,
            name="uvicorn-server",
        )
        self._thread.start()
        logger.info("Uvicorn server starting on %s:%s", HOST, PORT)

    def stop(self) -> None:
        if self._server is not None:
            logger.info("Stopping uvicorn server...")
            self._server.should_exit = True
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=5)
            logger.info("Server stopped.")


def wait_for_server() -> bool:
    deadline = time.time() + MAX_WAIT_SECONDS
    while time.time() < deadline:
        try:
            response = httpx.get(HEALTH_URL, timeout=1.0)
            if response.status_code == 200:
                logger.info("Server is ready.")
                return True
        except (httpx.ConnectError, httpx.TimeoutException):
            pass
        time.sleep(POLL_INTERVAL)

    logger.error("Server did not become ready within %s seconds.", MAX_WAIT_SECONDS)
    return False


def main() -> None:
    os.chdir(project_root)

    controller = ServerController()
    controller.start()

    if not wait_for_server():
        controller.stop()
        sys.exit(1)

    window = webview.create_window(
        "YouTube Semantic Search",
        f"http://{HOST}:{PORT}",
        width=1280,
        height=800,
        min_size=(1000, 700),
        js_api=Api(),
    )

    try:
        webview.start()
    finally:
        controller.stop()


if __name__ == "__main__":
    main()
