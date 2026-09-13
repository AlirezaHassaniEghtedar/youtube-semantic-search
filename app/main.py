import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import engine, ensure_columns
from app.models import Base
from app.routers import channels, chat, conversations, local_videos, search, videos
from app.schemas import HealthResponse
from app.services.embedder import EmbedderService
from app.services.transcriber import load_whisper_model

logger = logging.getLogger(__name__)

# Serve the Vite production build produced by frontend-react/ (see its
# package.json: `npm run build` outputs to ../dist). Build it before launching.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIST_DIR = PROJECT_ROOT / "dist"


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
app.include_router(local_videos.router)
app.include_router(search.router)
app.include_router(chat.router)
app.include_router(conversations.router)


@app.get("/api/health", response_model=HealthResponse)
async def health_check():
    return HealthResponse(status="ok")


if not FRONTEND_DIST_DIR.is_dir():
    raise RuntimeError(
        "Frontend build not found at %s. Build it first: "
        "cd frontend-react && npm install && npm run build" % FRONTEND_DIST_DIR
    )

app.mount("/static", StaticFiles(directory=str(FRONTEND_DIST_DIR)), name="static")


@app.get("/{full_path:path}")
async def serve_spa(full_path: str):
    """Serve the built SPA. Asset paths (/assets/*) resolve inside the dist
    directory; any unknown path falls back to index.html so client-side
    routes and deep links keep working."""
    candidate = FRONTEND_DIST_DIR / full_path
    if (
        full_path
        and candidate.is_file()
        and FRONTEND_DIST_DIR.resolve() in candidate.resolve().parents
    ):  # path traversal guard
        return FileResponse(candidate)
    return FileResponse(FRONTEND_DIST_DIR / "index.html")
