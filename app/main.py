import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import engine, ensure_columns
from app.models import Base
from app.routers import channels, search, videos
from app.schemas import HealthResponse
from app.services.embedder import EmbedderService
from app.services.transcriber import load_whisper_model

logger = logging.getLogger(__name__)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.data_path.mkdir(parents=True, exist_ok=True)
    settings.download_path.mkdir(parents=True, exist_ok=True)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await ensure_columns()

    logger.info("Loading Whisper model (%s)...", settings.WHISPER_MODEL_SIZE)
    whisper_model = load_whisper_model()
    app.state.whisper = whisper_model
    logger.info("Whisper model loaded.")

    logger.info("Loading embedding model (%s)...", settings.embedding_model_path)
    embedder = EmbedderService()
    embedder.load()
    app.state.embedder = embedder
    logger.info("Embedding model loaded.")

    yield

    await engine.dispose()


app = FastAPI(
    title="YouTube Semantic Search",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(channels.router)
app.include_router(videos.router)
app.include_router(search.router)


@app.get("/api/health", response_model=HealthResponse)
async def health_check():
    return HealthResponse(status="ok")


app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.get("/")
async def serve_index():
    return FileResponse(FRONTEND_DIR / "index.html")
