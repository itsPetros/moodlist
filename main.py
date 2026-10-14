import os
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote_plus

try:  # optional: load .env if python-dotenv is installed
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import catalog
import options
import youtube

STATIC = Path(__file__).resolve().parent / "static"
NO_STORE = {"Cache-Control": "no-store"}


@asynccontextmanager
async def lifespan(app):
    catalog.sync_seed()
    yield


app = FastAPI(title="MoodList", lifespan=lifespan)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


# ------------------------------------------------------------------ rate limit
# Simple per-IP limiter so one visitor can't hammer the server.
# Behind a proxy/host, start uvicorn with --proxy-headers so the real IP is seen.
_hits = defaultdict(deque)


def rate_limited(ip):
    limit = int(os.environ.get("RATE_LIMIT_PER_MIN", "30"))
    now = time.monotonic()
    q = _hits[ip]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= limit:
        return True
    q.append(now)
    return False


# ------------------------------------------------------------------------ API

@app.get("/api/options")
def get_options():
    return JSONResponse(options.as_dict(), headers={"Cache-Control": "public, max-age=3600"})


@app.get("/api/playlist")
def make_playlist(
    request: Request,
    mood: str,
    time_of_day: str | None = Query(None, alias="time"),
    weather: str | None = None,
    genre: str = "any",
    size: int = 12,
):
    if rate_limited(request.client.host if request.client else "unknown"):
        raise HTTPException(429, "Too many requests. Please wait a moment and try again.")
    if mood not in options.ids(options.MOODS):
        raise HTTPException(400, "Please pick a mood.")
    if time_of_day is not None and time_of_day not in options.ids(options.TIMES):
        raise HTTPException(400, "Unknown time of day.")
    if weather is not None and weather not in options.ids(options.WEATHERS):
        raise HTTPException(400, "Unknown weather.")
    if genre not in options.ids(options.GENRES):
        raise HTTPException(400, "Unknown genre.")
    size = max(10, min(15, size))

    # The goal is a YouTube playlist with ALL the songs in it, so only songs that already
    # have a matched video are used. If no video has been matched at all yet (no API key,
    # or build_catalog.py not run), fall back to showing songs as search links.
    matched_only = catalog.ok_count() > 0
    songs, note = catalog.pick_songs(mood, time_of_day, weather, genre, size, only_matched=matched_only)
    if not songs:
        raise HTTPException(404, "No songs available for this choice yet.")

    extra = None
    if not matched_only:
        extra = (
            "No YouTube videos have been matched yet, so the songs below are search links. "
            "Run build_catalog.py (see the README) to get the YouTube playlist button."
        )
    elif len(songs) < size:
        extra = (
            f"Only {len(songs)} songs for this choice have a YouTube video so far, so the playlist is "
            "shorter. More are added as the catalog fills in."
        )
    if extra:
        note = f"{note} {extra}" if note else extra

    tracks = [
        {
            "artist": s["artist"],
            "title": s["title"],
            "video_id": s["video_id"] if s["status"] == "ok" else None,
            "search_url": "https://www.youtube.com/results?search_query="
            + quote_plus(f"{youtube.first_artist(s['artist'])} {s['title']} official"),
        }
        for s in songs
    ]
    video_ids = [t["video_id"] for t in tracks if t["video_id"]]
    parts = [options.label(options.MOODS, mood)]
    if time_of_day:
        parts.append(options.label(options.TIMES, time_of_day))
    if weather:
        parts.append(options.label(options.WEATHERS, weather))
    if genre != "any":
        parts.append(options.label(options.GENRES, genre))

    body = {
        "title": " · ".join(parts),
        "note": note,
        "tracks": tracks,
        "total": len(tracks),
        "playlist_count": len(video_ids[:50]),  # songs that are in the YouTube link
        # Undocumented YouTube link that opens a list of videos as a temporary playlist.
        "youtube_playlist_url": (
            "https://www.youtube.com/watch_videos?video_ids=" + ",".join(video_ids[:50])
            if len(video_ids) >= 2
            else None
        ),
    }
    return JSONResponse(body, headers=NO_STORE)


@app.get("/healthz")
def healthz():
    return {"ok": True}


# ------------------------------------------------------------- pages and files

app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/sw.js")
def service_worker():
    return FileResponse(
        STATIC / "sw.js", media_type="application/javascript", headers={"Cache-Control": "no-cache"}
    )
