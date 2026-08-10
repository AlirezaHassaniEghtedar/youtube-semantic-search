from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    DATABASE_URL: str = "sqlite+aiosqlite:///./data/app.db"
    WHISPER_MODEL_SIZE: str = "base"
    WHISPER_DEVICE: str = "cuda"
    WHISPER_COMPUTE_TYPE: str = "float16"
    WHISPER_NUM_WORKERS: int = 2
    # Keep download parallelism conservative: it is the main contributor to
    # YouTube rate limits. Whisper work is local and can still use the GPU.
    MAX_CONCURRENT_DOWNLOADS: int = 2
    MAX_CONCURRENT_TRANSCRIBE: int = 2
    PREFER_CAPTIONS: bool = True
    YT_COOKIES_FROM_BROWSER: str | None = None
    YT_COOKIES_FILE: str | None = None
    # Backwards-compatible alias for the earlier documented setting name.
    YT_DLP_COOKIES_FROM_BROWSER: str | None = None
    DOWNLOAD_JITTER_MIN_SECONDS: float = 1.0
    DOWNLOAD_JITTER_MAX_SECONDS: float = 4.0
    BOT_CHECK_COOLDOWN_MINUTES: int = 15
    # Shared across all channel listings and audio downloads in this process.
    YT_GLOBAL_MIN_INTERVAL_SECONDS: float = 2.0
    EMBEDDING_MODEL: str = (
        "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    )
    DOWNLOAD_DIR: str = "./downloads"
    MAX_SEARCH_RESULTS: int = 20
    DEFAULT_TIME_WINDOW: str = "7d"

    @property
    def download_path(self) -> Path:
        path = Path(self.DOWNLOAD_DIR)
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def data_path(self) -> Path:
        path = Path("data")
        path.mkdir(parents=True, exist_ok=True)
        return path


settings = Settings()
