# Run doc — YouTube Semantic Search (React frontend migration)

## Reproduce artifacts (fresh checkout)

1. Python deps + venv: create `.venv` and `pip install -r requirements.txt`
   (backend runs from the repo root with `python run_app.py`, which boots
   uvicorn on `127.0.0.1:8000`).
2. Frontend build: `cd frontend-react && npm install` (Node 24 / npm 11 are
   current). Then `npm run build` — this typechecks (`tsc --noEmit`) and
   outputs the production bundle to `../dist/` (Vite `outDir: "../dist"`,
   `emptyOutDir: true`).
3. If `data/app.db` is absent, it is created automatically on first backend
   start. If you have an existing one from the main checkout, copy `data/`
   to keep channels/videos/conversations.

## Run the server

1. Start the backend (it serves the built SPA from `dist/` at `/`):
   `.venv/Scripts/python.exe run_app.py` — BUT `run_app.py` opens the native
   pywebview window, which blocks in a terminal. For a headless server:
   `.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000`
   from the repo root. Wait for `/api/health` to return 200.
2. Open `http://127.0.0.1:8000/` — the React app loads from `dist/`.
3. Frontend dev mode (optional, hot reload): `cd frontend-react && npm run dev`
   → `http://127.0.0.1:5173` (proxies `/api` to `127.0.0.1:8000`).

## Notes

- `app/main.py` serves ONLY `dist/` (the Vite output) via a catch-all SPA
  route. The legacy vanilla `frontend/` folder has been deleted; if `dist/`
  is missing the backend refuses to start with a clear error — build first.
- `dist/` is git-ignored, so a fresh checkout MUST run the frontend build
  step before launching (see "Reproduce artifacts" above).
- The pywebview `Api` class in `run_app.py` is unchanged — the React app calls
  `window.pywebview.api.select_video_file()/select_subtitle_file()` exactly
  like the vanilla app did.
- Preview server log: `.freebuff/preview-*.log` in this repo.
