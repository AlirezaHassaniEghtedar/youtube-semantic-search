# YouTube Semantic Search — Desktop App

A local Windows desktop application for semantic search across YouTube channel transcripts. Add a channel, select a time window, and the app downloads audio, transcribes speech (Persian + English), indexes segments with embeddings, and lets you search by meaning — not just keywords.

Everything runs offline after the initial model download. Vector search uses pure NumPy cosine similarity — no external vector database.

---

## Prerequisites

| Requirement | Details |
|---|---|
| **Python** | 3.11 or newer |
| **OS** | Windows 10 / 11 |
| **FFmpeg** | Required by yt-dlp for audio extraction — must be in your system PATH |
| **Internet** | Needed once to download Whisper + embedding models and YouTube content |
| **Deno** | Required by current yt-dlp for YouTube JavaScript challenge solving; install separately and put `deno` on PATH |
| **bgutil-ytdlp-pot-provider** | Installed by `requirements.txt`; its companion HTTP server must be running on `http://127.0.0.1:4416` (see Setup) |
| **NVIDIA GPU (optional)** | GPU acceleration needs a reasonably recent CUDA 12-compatible NVIDIA driver. The pip dependencies provide the runtime; no separate CUDA Toolkit is required. |

### Install FFmpeg

**Via winget (recommended):**

```powershell
winget install Gyan.FFmpeg
```

**Via Chocolatey:**

```powershell
choco install ffmpeg
```

Verify installation:

```powershell
ffmpeg -version
```

---

## Setup

1. **Clone or copy** this project to your machine.

2. **Create a virtual environment:**

   ```powershell
   cd youtube_search_desktop
   python -m venv venv
   ```

3. **Activate the virtual environment:**

   ```powershell
   venv\Scripts\activate
   ```

4. **Install dependencies:**

   ```powershell
   pip install -r requirements.txt
   ```

   If logs show Whisper falling back to CPU after installation, update your NVIDIA driver from the NVIDIA website.

5. **Install Deno and start the bgutil PO-token provider** (required for yt-dlp YouTube access):

   This app does **not** start the provider for you. Leave it running in a separate terminal whenever you sync channels.

   **Install Deno:**

   ```powershell
   winget install DenoLand.Deno
   ```

   Close and reopen the terminal, then verify `deno --version`.

   **Prepare the bgutil provider server** (version should match the installed Python plugin):

   ```powershell
   $BgutilVersion = python -c "import importlib.metadata as m; print(m.version('bgutil-ytdlp-pot-provider'))"
   git clone https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git "$env:USERPROFILE\bgutil-ytdlp-pot-provider"
   cd "$env:USERPROFILE\bgutil-ytdlp-pot-provider"
   git checkout $BgutilVersion
   cd server
   deno install --allow-scripts=npm:canvas --frozen
   ```

   Start the provider and leave it running:

   ```powershell
   cd "$env:USERPROFILE\bgutil-ytdlp-pot-provider\server"
   deno run --allow-env --allow-net --allow-ffi=node_modules --allow-read=node_modules src/main.ts --port 4416
   ```

   You should see it listen on port `4416`. The app talks to `http://127.0.0.1:4416` by default (`YT_POT_PROVIDER_BASE_URL`).

6. **Configure environment:**

   ```powershell
   copy .env.example .env
   ```

   Edit `.env` if you want to change the Whisper model size, embedding model, or other settings.

---

## Launch

Double-click **`run_app.bat`**.

The app opens in a native Windows window (powered by pywebview). No browser tab or terminal window is shown to the end user.

Alternatively, from an activated virtual environment:

```powershell
python run_app.py
```

On first launch, Whisper and sentence-transformers models are downloaded automatically. This requires internet access once. After that, transcription and search work fully offline.

---

## Usage Walkthrough

### 1. Add a Channel

1. Paste a YouTube channel URL (e.g. `https://www.youtube.com/@channelname`).
2. Choose a **time window**:
   - **Last 24 hours** / **7 days** / **30 days**
   - **Custom range** — pick start and end dates
   - **Custom hours** — process videos from the last number of hours you enter
   - **All videos** — process the entire channel catalog
3. Click **Add Channel**.

The app lists matching videos immediately and begins processing in the background: download → transcribe → embed. Progress is shown with status badges on each channel and video.

### 2. Monitor Progress

The channels list auto-refreshes every 3 seconds while any channel is still processing. Each card shows:

- Channel name and colored status badge
- **X/Y done** count (completed vs total videos)
- **View Videos** and **Delete** buttons

### 3. Browse Videos

Click **View Videos** on a channel card to see a table of all videos in the selected window — title, publish date, duration, and per-video status.

When a video reaches **done**, click **View Transcript** to open the transcript viewer.

### 4. View Transcripts

The transcript modal supports two modes:

- **With timestamps** — each line shows `[mm:ss]` (or `[hh:mm:ss]` for long videos). Click a timestamp to open YouTube at that exact second.
- **Without timestamps** — clean merged text with a **Copy text** button.

Persian text renders right-to-left automatically.

### 5. Semantic Search

Enter a query in Persian or English. Optionally filter by channel and date range. Results show:

- Channel name and video title
- Matching transcript snippet
- Timestamp and similarity percentage
- **▶ Watch** button linking to the exact moment on YouTube

---

## How Search Works

Embeddings are stored as float32 BLOBs in SQLite. At query time, candidate segments are loaded into a NumPy matrix and ranked by cosine similarity (dot product on L2-normalized vectors). No Faiss, sqlite-vec, pgvector, or other vector index is used.

This approach is fast at personal scale (hundreds to low thousands of segments) and keeps the stack simple.

---

## Configuration

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `sqlite+aiosqlite:///./data/app.db` | SQLite database path |
| `WHISPER_MODEL_SIZE` | `base` | faster-whisper model size (`tiny`, `base`, `small`, `medium`, `large-v3`) |
| `WHISPER_DEVICE` | `cuda` | Whisper device (`cuda` or `cpu`); CUDA falls back to CPU if unavailable |
| `WHISPER_COMPUTE_TYPE` | `float16` | CUDA Whisper compute type |
| `WHISPER_NUM_WORKERS` | `2` | Concurrent Whisper model workers |
| `MAX_CONCURRENT_DOWNLOADS` | `2` | Maximum simultaneous audio downloads; raising it increases YouTube rate-limit risk |
| `MAX_CONCURRENT_TRANSCRIBE` | `2` | Maximum simultaneous Whisper transcriptions |
| `PREFER_CAPTIONS` | `true` | Use existing YouTube captions before downloading audio and transcribing |
| `YT_COOKIES_FROM_BROWSER` | empty | Optional browser-cookie fallback for yt-dlp; a static cookie file is more reliable |
| `YT_COOKIES_FILE` | empty | Recommended: Netscape-format `cookies.txt` file for authenticated yt-dlp requests |
| `DOWNLOAD_JITTER_MIN_SECONDS` / `DOWNLOAD_JITTER_MAX_SECONDS` | `1.0` / `4.0` | Random delay before downloads to avoid request bursts |
| `BOT_CHECK_COOLDOWN_MINUTES` | `15` | How long a batch backs off after a bot-check response |
| `YT_GLOBAL_MIN_INTERVAL_SECONDS` | `2.0` | Minimum spacing between every yt-dlp listing or download in one app session |
| `EMBEDDING_MODEL` | `paraphrase-multilingual-MiniLM-L12-v2` | sentence-transformers model |
| `DOWNLOAD_DIR` | `./downloads` | Temporary audio storage (deleted after transcription) |
| `MAX_SEARCH_RESULTS` | `20` | Default search result limit |
| `DEFAULT_TIME_WINDOW` | `7d` | Default time window |
| `YOUTUBE_DATA_API_KEY` | empty | Optional YouTube Data API v3 key for full metadata coverage |
| `YT_POT_PROVIDER_BASE_URL` | `http://127.0.0.1:4416` | Local bgutil PO-token HTTP server used by yt-dlp |

### YouTube Data API Setup (Optional)

The app uses the YouTube Data API v3 when configured to fetch accurate publication dates and durations for **every video** in a channel. Without the API, metadata is limited to the ~15 most recent videos (via RSS) and duration estimates from yt-dlp's playlist scraping.

**Benefits of the API key:**
- Accurate `published_at` for all videos (not just recent ones)
- Accurate `duration_seconds` for all videos
- Direct `scheduledStartTime` for upcoming/scheduled streams (improves PART 2 enrichment)
- Full metadata coverage regardless of channel size or sync window

**Setup:**

1. **Create a Google Cloud project:**
   - Go to [Google Cloud Console](https://console.cloud.google.com)
   - Create a new project (or use an existing one)

2. **Enable YouTube Data API v3:**
   - Search for "YouTube Data API v3" in the API library
   - Click **Enable**

3. **Create an API key credential:**
   - Go to **Credentials** → **Create Credentials** → **API Key**
   - Copy the generated key

4. **Add to `.env`:**
   ```
   YOUTUBE_DATA_API_KEY=YOUR_API_KEY_HERE
   ```

5. **Verify it works:**
   - Add a new channel with a large time window (e.g., "All videos")
   - Check logs for `date_filter_mode=youtube_api` to confirm the API is being used
   - All videos should have both `published_at` and `duration_seconds` populated

**Quota and costs:**
- Free tier: **10,000 quota units per calendar day**
- Each `channels.list` call (resolve uploads playlist): **1 unit**
- Each `playlistItems.list` call (fetch videos, max 50 per call): **1 unit**
- Each `videos.list` call (durations/live status, max 50 per call): **1 unit**
- A full channel sync with API typically costs **2–10 units** depending on size
- No billing is required for the free tier; usage is capped at 10,000 units/day

**Fallback behavior:**
- If the API key is not set, the app uses RSS + yt-dlp (existing behavior)
- If the API key is set but quota is exceeded, the app falls back to RSS + yt-dlp automatically
- The app does not retry API calls once quota is exhausted in the same session

---

## Reducing YouTube rate-limit risk

The best defense is to use a logged-in YouTube session through a static cookies file:

1. Install a browser extension such as **Get cookies.txt LOCALLY**.
2. Log into YouTube in that browser and export the cookies for `youtube.com`.
3. Save the export as `cookies.txt` in the project root.
4. Set `YT_COOKIES_FILE=./cookies.txt` in `.env` and keep the file private. It contains live session credentials and is ignored by Git.

This is more reliable for a long-running desktop app than `cookiesfrombrowser`, which may fail while a browser has its cookie database locked. Authenticated automated use remains your responsibility and may carry account and YouTube Terms-of-Service risk.

Keep `MAX_CONCURRENT_DOWNLOADS` conservative (the default is `2`). Channel listing walks `/videos`, `/shorts`, and `/streams` with per-tab caps, using yt-dlp `approximate_date` plus optional RSS overlay. If YouTube still reports a bot check, the app pauses the affected channel and increases the request spacing for the rest of the session; cookies remain the durable remedy.

Captions are fetched with yt-dlp subtitle download (not `youtube-transcript-api`). Keep the bgutil provider running so those requests can obtain a PO token.

---

## Project Structure

```
youtube_search_desktop/
├── run_app.py          # Desktop launcher (pywebview + uvicorn)
├── run_app.bat         # Double-click launcher
├── requirements.txt
├── .env / .env.example
├── data/               # SQLite database (auto-created)
├── downloads/          # Temp audio files (auto-cleaned)
├── app/                # FastAPI backend
└── frontend/           # HTML/CSS/JS UI
```

---

## Optional: Package as a Standalone .exe

You can bundle the app with PyInstaller for distribution without requiring Python on the target machine.

1. Install PyInstaller:

   ```powershell
   pip install pyinstaller
   ```

2. Build (from the project root with venv activated):

   ```powershell
   pyinstaller --onefile --windowed ^
     --add-data "frontend;frontend" ^
     --add-data ".env;." ^
     --hidden-import=faster_whisper ^
     --hidden-import=sentence_transformers ^
     --name "YouTubeSemanticSearch" ^
     run_app.py
   ```

3. Copy the `data/` and `downloads/` folders next to the generated `.exe`, or let the app create them on first run.

4. Ensure FFmpeg is installed on the target machine.

> Note: The first run on a new machine still downloads ML models (~100–500 MB depending on Whisper size). Bundle models separately or pre-populate the Hugging Face cache for fully offline first-run.

---

## Troubleshooting

| Issue | Solution |
|---|---|
| **FFmpeg not found** | Install FFmpeg and ensure `ffmpeg` is in PATH. Restart the terminal/app. |
| **Model download fails** | Check internet connection. Retry after connectivity is restored. |
| **Video shows Error status** | Video may be private, deleted, or age-restricted. Other videos continue processing. |
| **Slow transcription** | Use a smaller Whisper model (`tiny` or `base`) in `.env`. CPU transcription is inherently slow. |
| **"Sign in to confirm you're not a bot" or many videos fail together** | YouTube has rate-limited the IP. Wait before retrying, keep `MAX_CONCURRENT_DOWNLOADS` low, and preferably configure `YT_COOKIES_FILE` using the steps above. |
| **App window blank on launch** | Wait up to 15 seconds for models to load. Check logs in the terminal if running manually. |

---

## License

This project is provided as-is for personal and educational use.
