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
| **Internet** | Needed to download models and YouTube content |
| **Deno** | Required by current yt-dlp for YouTube JavaScript challenge solving; install separately and put `deno` on PATH |
| **Git** | Required only for the manual bgutil provider setup below; you can alternatively download the repository ZIP |
| **bgutil-ytdlp-pot-provider** | Installed by `requirements.txt`; its local provider server must be prepared and started separately for the optional PO-token path |
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

5. **Install the external YouTube runtime prerequisites:**

   The application does **not** download or install Deno or bgutil automatically. This keeps application startup predictable and avoids modifying the machine behind the user's back.

   **Install Deno** (recommended JavaScript runtime for yt-dlp):

   ```powershell
   winget install DenoLand.Deno
   ```

   Close and reopen the terminal after installation, then verify:

   ```powershell
   deno --version
   ```

   Current yt-dlp enables Deno automatically when it is available on `PATH`. The project does not pass a custom `js_runtimes` dictionary, so it avoids the `Invalid js_runtimes format` error.

   **Prepare the bgutil POT provider:**

   `bgutil-ytdlp-pot-provider` is already installed by `requirements.txt`. The provider server itself must be downloaded and prepared separately. Keep the server version aligned with the installed Python plugin:

   ```powershell
   $BgutilVersion = python -c "import importlib.metadata as m; print(m.version('bgutil-ytdlp-pot-provider'))"
   git clone https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git "$env:USERPROFILE\bgutil-ytdlp-pot-provider"
   cd "$env:USERPROFILE\bgutil-ytdlp-pot-provider"
   git checkout $BgutilVersion
   cd server
   deno install --allow-scripts=npm:canvas --frozen
   ```

   Start the provider in a separate terminal and leave it running while the application uses YouTube:

   ```powershell
   cd "$env:USERPROFILE\bgutil-ytdlp-pot-provider\server"
   deno run --allow-env --allow-net --allow-ffi=node_modules --allow-read=node_modules src/main.ts --port 4416
   ```

   You should see the provider start on port `4416`. The application uses the provider's default local endpoint when the bgutil plugin is available.

   Verify that the Python environment can see the plugin:

   ```powershell
   python -c "import importlib.metadata as m; print('bgutil:', m.version('bgutil-ytdlp-pot-provider'))"
   ```

6. **Configure environment:**

   ```powershell
   copy .env.example .env
   ```

   Edit `.env` if you want to change the Whisper model size, embedding model, or other settings.


7. **login youtube:**  
You need to login to YouTube in Firefox browser. 
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
| `YT_COOKIES_FROM_BROWSER` | empty | Optional logged-in browser cookies (`chrome`, `firefox`, `edge`, or `brave`) for yt-dlp |
| `YT_COOKIES_FILE` | empty | Optional Netscape-format cookies.txt file, used instead of browser cookies |
| `DOWNLOAD_JITTER_MIN_SECONDS` / `DOWNLOAD_JITTER_MAX_SECONDS` | `1.0` / `4.0` | Random delay before downloads to avoid request bursts |
| `BOT_CHECK_COOLDOWN_MINUTES` | `15` | How long a batch backs off after a bot-check response |
| `EMBEDDING_MODEL` | `paraphrase-multilingual-MiniLM-L12-v2` | sentence-transformers model |
| `DOWNLOAD_DIR` | `./downloads` | Temporary audio storage (deleted after transcription) |
| `MAX_SEARCH_RESULTS` | `20` | Default search result limit |
| `DEFAULT_TIME_WINDOW` | `7d` | Default time window |

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
| **"Sign in to confirm you're not a bot" or many videos fail together** | YouTube has rate-limited the IP. Wait before retrying, keep `MAX_CONCURRENT_DOWNLOADS` low, and optionally set `YT_COOKIES_FROM_BROWSER=chrome` (or another browser where you are logged in). Cookie-based automated access is your responsibility and may carry account/Terms-of-Service risk. |
| **App window blank on launch** | Wait up to 15 seconds for models to load. Check logs in the terminal if running manually. |

---

## YouTube transcript reliability (current)

The application uses a layered transcript pipeline:

1. `youtube-transcript-api` for a fast direct transcript request.
2. `yt-dlp` for a second independent caption path.
3. `faster-whisper` only when captions are unavailable or a caption provider requires a PO token.

### External yt-dlp runtime setup

The application intentionally does **not** bootstrap Deno or bgutil at runtime. Install these components once on the machine as described in the Setup section.

- `yt-dlp[default]` is installed from PyPI without a version pin, so `pip install -U -r requirements.txt` gets the current compatible yt-dlp release and its default dependencies.
- Deno is discovered from the normal system `PATH`; yt-dlp enables Deno automatically.
- `bgutil-ytdlp-pot-provider` is installed from PyPI without a version pin, and its provider server is run separately on `127.0.0.1:4416`.
- The application does not inject a `js_runtimes` dictionary or download a project-local runtime.

If Deno is missing, the application logs a clear warning. YouTube may then have reduced compatibility until Deno is installed.

### Caption-first behavior

When `PREFER_CAPTIONS=true`, the pipeline attempts captions before downloading audio. A successful subtitle file is parsed directly and Whisper is not invoked. Only when captions return no usable segments or are temporarily blocked does the pipeline move to audio download and Whisper transcription.

### Important limitation

YouTube may enforce Proof-of-Origin (PO) tokens for some requests. A PO-token failure is different from a rate limit and cannot be fixed by blindly retrying the same URL. The application uses the local bgutil provider when available and falls back to Whisper when caption retrieval is unavailable but audio access still works.

## License

This project is provided as-is for personal and educational use.
