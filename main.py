import os
import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List
from uuid import UUID
from urllib.parse import quote_plus

from fastapi import FastAPI, Depends, HTTPException, status, Query, Request, File, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from google.oauth2 import id_token
from google.auth.transport import requests
from dotenv import load_dotenv

import catalog
import options
import youtube
import models
import schemas
import auth
from database import engine, Base, get_db

load_dotenv()

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")

STATIC = Path(__file__).resolve().parent / "static"
UPLOAD_DIR = Path("uploads/profile_pics")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

NO_STORE = {"Cache-Control": "no-store"}
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MAX_FILE_SIZE = 2 * 1024 * 1024  # 2 MB
HISTORY_LIMIT = 50  # newest playlists kept per user


@asynccontextmanager
async def lifespan(app: FastAPI):
    catalog.sync_seed()
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="MoodList", lifespan=lifespan)

app.mount("/static/profile_pics", StaticFiles(directory=UPLOAD_DIR), name="profile_pics")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


# Rate Limiter
_hits = defaultdict(deque)


def rate_limited(ip: str) -> bool:
    limit = int(os.environ.get("RATE_LIMIT_PER_MIN", "60"))
    now = time.monotonic()
    q = _hits[ip]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= limit:
        return True
    q.append(now)
    return False


# --- AUTHENTICATION & USER ENDPOINTS ---

@app.post("/auth/google")
def google_auth(data: dict, db: Session = Depends(get_db)):
    token = data.get("credential")
    if not token:
        raise HTTPException(status_code=400, detail="Missing credential token")

    try:
        id_info = id_token.verify_oauth2_token(
            token, 
            requests.Request(), 
            GOOGLE_CLIENT_ID,
            clock_skew_in_seconds=60
        )

        user_email = id_info["email"]
        user_name = id_info.get("name", user_email.split("@")[0])

        user = db.query(models.User).filter(models.User.email == user_email).first()

        if not user:
            user = models.User(
                email=user_email,
                username=user_name,
                hashed_password=None,
                is_verified=True
            )
            db.add(user)
            db.commit()
            db.refresh(user)

        access_token = auth.create_access_token(data={"sub": user.username})
        return {"access_token": access_token, "token_type": "bearer"}

    except ValueError as e:
        print(f"Google Auth Error: {e}")
        raise HTTPException(status_code=400, detail="Invalid Google token")


@app.get("/users/me", response_model=schemas.UserResponse)
def get_me(current_user: models.User = Depends(auth.get_current_user)):
    return current_user


@app.post("/users/me/avatar", status_code=status.HTTP_200_OK)
async def upload_profile_picture(
    file: UploadFile = File(...),
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    file_ext = Path(file.filename).suffix.lower()
    if file_ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Invalid extension")

    contents = await file.read()
    if len(contents) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="File exceeds 2MB limit")

    unique_filename = f"{current_user.id}_{uuid.uuid4().hex}{file_ext}"
    file_path = UPLOAD_DIR / unique_filename

    with open(file_path, "wb") as f:
        f.write(contents)

    if current_user.profile_picture_url:
        old_filename = current_user.profile_picture_url.split("/")[-1]
        old_file_path = UPLOAD_DIR / old_filename
        if old_file_path.exists():
            try:
                os.remove(old_file_path)
            except OSError:
                pass

    profile_url = f"/static/profile_pics/{unique_filename}"
    current_user.profile_picture_url = profile_url
    db.commit()
    db.refresh(current_user)
    return {"profile_picture_url": profile_url}


# --- PLAYLIST MANAGEMENT ---

@app.post("/playlists", response_model=schemas.PlaylistResponse, status_code=status.HTTP_201_CREATED)
def save_playlist(
    playlist_in: schemas.PlaylistCreate,
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    playlist = models.SavedPlaylist(
        user_id=current_user.id,
        title=playlist_in.title,
        mood=playlist_in.mood,
        genre=playlist_in.genre,
        weather=playlist_in.weather,
        time_of_day=playlist_in.time_of_day,
        song_ids=playlist_in.song_ids,
        is_public=playlist_in.is_public
    )
    db.add(playlist)
    db.commit()
    db.refresh(playlist)
    return playlist


def _watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


@app.get("/users/me/playlists", response_model=List[schemas.PlaylistResponse])
def get_my_playlists(
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    rows = (
        db.query(models.SavedPlaylist)
        .filter(models.SavedPlaylist.user_id == current_user.id)
        .order_by(models.SavedPlaylist.created_at.desc())
        .all()
    )
    known = catalog.lookup_videos([v for p in rows for v in p.song_ids])
    out = []
    for p in rows:
        item = schemas.PlaylistResponse.model_validate(p)
        item.tracks = [
            schemas.TrackOut(
                artist=known.get(v, {}).get("artist", ""),
                title=known.get(v, {}).get("title", "Song on YouTube"),
                video_id=v,
                url=_watch_url(v),
            )
            for v in p.song_ids
        ]
        out.append(item)
    return out


@app.delete("/users/me/playlists/{playlist_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_my_playlist(
    playlist_id: UUID,
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    playlist = (
        db.query(models.SavedPlaylist)
        .filter(models.SavedPlaylist.id == playlist_id, models.SavedPlaylist.user_id == current_user.id)
        .first()
    )
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")
    db.delete(playlist)
    db.commit()


# --- PLAYLIST HISTORY ---

@app.get("/users/me/history", response_model=List[schemas.HistoryResponse])
def get_my_history(
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    return (
        db.query(models.PlaylistHistory)
        .filter(models.PlaylistHistory.user_id == current_user.id)
        .order_by(models.PlaylistHistory.created_at.desc())
        .limit(HISTORY_LIMIT)
        .all()
    )


@app.delete("/users/me/history", status_code=status.HTTP_204_NO_CONTENT)
def clear_my_history(
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    db.query(models.PlaylistHistory).filter(models.PlaylistHistory.user_id == current_user.id).delete()
    db.commit()


@app.delete("/users/me/history/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_history_entry(
    entry_id: UUID,
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    entry = (
        db.query(models.PlaylistHistory)
        .filter(models.PlaylistHistory.id == entry_id, models.PlaylistHistory.user_id == current_user.id)
        .first()
    )
    if not entry:
        raise HTTPException(status_code=404, detail="History entry not found")
    db.delete(entry)
    db.commit()


def _record_history(db: Session, user: models.User, title, mood, genre, weather, time_of_day, tracks):
    """Store a generated playlist for a signed-in user. Never lets a failure break generation."""
    try:
        db.add(models.PlaylistHistory(
            user_id=user.id,
            title=title[:200],
            mood=mood,
            genre=genre,
            weather=weather,
            time_of_day=time_of_day,
            tracks=[
                {
                    "artist": t["artist"],
                    "title": t["title"],
                    "video_id": t["video_id"],
                    "url": _watch_url(t["video_id"]) if t["video_id"] else t["search_url"],
                }
                for t in tracks
            ],
        ))
        db.flush()
        stale = (
            db.query(models.PlaylistHistory.id)
            .filter(models.PlaylistHistory.user_id == user.id)
            .order_by(models.PlaylistHistory.created_at.desc())
            .offset(HISTORY_LIMIT)
            .all()
        )
        if stale:
            db.query(models.PlaylistHistory).filter(
                models.PlaylistHistory.id.in_([r.id for r in stale])
            ).delete(synchronize_session=False)
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        print(f"Could not record playlist history: {exc}")

# --- PLAYLIST GENERATOR & STATIC FILES ---

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
    user: models.User | None = Depends(auth.get_optional_user),
    db: Session = Depends(get_db),
):
    if rate_limited(request.client.host if request.client else "unknown"):
        raise HTTPException(429, "Too many requests. Please wait a moment and try again.")
    if mood not in options.ids(options.MOODS):
        raise HTTPException(400, "Please pick a mood.")

    matched_only = catalog.ok_count() > 0
    songs, note = catalog.pick_songs(mood, time_of_day, weather, genre, size, only_matched=matched_only)
    if not songs:
        raise HTTPException(404, "No songs available for this choice yet.")

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
        "playlist_count": len(video_ids[:50]),
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


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/profile")
def profile_page():
    return FileResponse(STATIC / "profile.html", headers={"Cache-Control": "no-cache"})

@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/sw.js")
def service_worker():
    return FileResponse(STATIC / "sw.js", media_type="application/javascript", headers={"Cache-Control": "no-cache"})