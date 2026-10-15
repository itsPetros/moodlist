import os
import time
import random
import string
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import List
from urllib.parse import quote_plus
import resend

from fastapi import FastAPI, Depends, HTTPException, status, Query, Request, File, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

import catalog
import options
import youtube
import models, schemas, auth
from database import engine, Base, get_db
from dotenv import load_dotenv
from google.oauth2 import id_token
from google.auth.transport import requests
from jose import jwt

load_dotenv() 
resend.api_key = os.getenv("RESEND_API_KEY")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")

SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-change-this-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7 days

STATIC = Path(__file__).resolve().parent / "static"
UPLOAD_DIR = Path("uploads/profile_pics")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
NO_STORE = {"Cache-Control": "no-store"}
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MAX_FILE_SIZE = 2 * 1024 * 1024  # 2 MB

def generate_6digit_code() -> str:
    return "".join(random.choices(string.digits, k=6))

def send_verification_email(to_email: str, code: str, subject: str = "Your MoodList Verification Code"):
    if not resend.api_key:
        print(f"[DEMO MODE] RESEND_API_KEY missing. Code for {to_email}: {code}")
        return

    try:
        response = resend.Emails.send({
            "from": "MoodList <onboarding@resend.dev>",
            "to": [to_email],
            "subject": subject,
            "html": f"""
            <div style="font-family: Arial, sans-serif; padding: 20px; background: #0f1115; color: #ffffff; border-radius: 8px;">
                <h2 style="color: #6366f1;">Welcome to MoodList!</h2>
                <p>Use the code below to complete your registration:</p>
                <div style="font-size: 32px; font-weight: bold; letter-spacing: 6px; color: #a855f7; margin: 20px 0;">
                    {code}
                </div>
                <p style="color: #8a8f9d; font-size: 14px;">This code will expire in 15 minutes.</p>
            </div>
            """
        })
        print(f"Verification email delivered to {to_email}. ID: {response['id']}")
    except Exception as e:
        print(f"Failed to send email to {to_email}: {e}")

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

# --- AUTH & USER ENDPOINTS ---

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
            clock_skew_in_seconds=10
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

        # USE auth.create_access_token TO MATCH auth.py VALIDATION
        access_token = auth.create_access_token(data={"sub": user.username})
        return {"access_token": access_token, "token_type": "bearer"}

    except ValueError as e:
        print(f"Google Auth Error: {e}")
        raise HTTPException(status_code=400, detail="Invalid Google token")


@app.post("/auth/register", status_code=status.HTTP_201_CREATED)
def register(user_data: schemas.UserRegister, db: Session = Depends(get_db)):
    if db.query(models.User).filter(models.User.username == user_data.username).first():
        raise HTTPException(status_code=400, detail="Username already exists")
    if db.query(models.User).filter(models.User.email == user_data.email).first():
        raise HTTPException(status_code=400, detail="Email already exists")

    code = generate_6digit_code()
    expires = datetime.utcnow() + timedelta(minutes=15)

    user = models.User(
        username=user_data.username,
        email=user_data.email,
        verification_code=code,
        verification_expires_at=expires,
        is_verified=False
    )
    db.add(user)
    db.commit()

    send_verification_email(user_data.email, code, "Verify your MoodList account")
    return {"message": "Verification code sent to your email."}


@app.post("/auth/set-password", response_model=schemas.UserResponse)
def verify_code_and_set_password(data: schemas.SetPasswordRequest, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == data.email).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user.verification_code != data.code or user.verification_expires_at < datetime.utcnow():
        raise HTTPException(status_code=400, detail="Invalid or expired verification code")

    user.hashed_password = auth.get_password_hash(data.password)
    user.is_verified = True
    user.verification_code = None
    user.verification_expires_at = None

    db.commit()
    db.refresh(user)
    return user


@app.post("/login", response_model=schemas.Token)
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.username == form_data.username).first()
    if not user or not user.is_verified or not auth.verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials or account not verified",
        )

    access_token = create_access_token(data={"sub": user.username})
    return {"access_token": access_token, "token_type": "bearer"}


@app.post("/auth/forgot-password", status_code=status.HTTP_200_OK)
def request_password_reset(data: schemas.ForgotPasswordRequest, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == data.email).first()
    success_msg = {"message": "If an account exists with this email, a verification code has been sent."}
    if not user:
        return success_msg

    reset_code = generate_6digit_code()
    user.verification_code = reset_code
    user.verification_expires_at = datetime.utcnow() + timedelta(minutes=15)
    db.commit()

    send_verification_email(data.email, reset_code, "Reset your MoodList password")
    return success_msg


@app.post("/auth/reset-password", status_code=status.HTTP_200_OK)
def reset_password(data: schemas.ResetPasswordRequest, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == data.email).first()
    if not user or not user.verification_code or user.verification_code != data.code:
        raise HTTPException(status_code=400, detail="Invalid or expired verification code")

    if user.verification_expires_at < datetime.utcnow():
        raise HTTPException(status_code=400, detail="Verification code has expired")

    user.hashed_password = auth.get_password_hash(data.new_password)
    user.verification_code = None
    user.verification_expires_at = None
    db.commit()

    return {"message": "Password reset successfully. You can now log in."}


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


@app.get("/users/me/playlists", response_model=List[schemas.PlaylistResponse])
def get_my_playlists(
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    return db.query(models.SavedPlaylist).filter(models.SavedPlaylist.user_id == current_user.id).all()


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

@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")

@app.get("/sw.js")
def service_worker():
    return FileResponse(STATIC / "sw.js", media_type="application/javascript", headers={"Cache-Control": "no-cache"})