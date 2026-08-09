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
    )

    try:
        webview.start()
    finally:
        controller.stop()


if __name__ == "__main__":
    main()
