import os
import random
import string
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import List
from uuid import UUID

from fastapi import FastAPI, Depends, HTTPException, status, File, UploadFile
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

import models, schemas, auth
from database import engine, Base, get_db

Base.metadata.create_all(bind=engine)

app = FastAPI()

# Avatar Uploads Setup
UPLOAD_DIR = Path("uploads/profile_pics")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static/profile_pics", StaticFiles(directory=UPLOAD_DIR), name="profile_pics")

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MAX_FILE_SIZE = 2 * 1024 * 1024  # 2 MB

def generate_6digit_code() -> str:
    return "".join(random.choices(string.digits, k=6))


# --- 1. SIGNUP & INITIAL VERIFICATION ---

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

    return {"message": "Verification code generated", "verification_code": code}


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

    access_token = auth.create_access_token(data={"sub": user.username})
    return {"access_token": access_token, "token_type": "bearer"}


# --- 2. FORGOT & RESET PASSWORD FLOW ---

@app.post("/auth/forgot-password", status_code=status.HTTP_200_OK)
def request_password_reset(data: schemas.ForgotPasswordRequest, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == data.email).first()
    
    success_msg = {"message": "If an account exists with this email, a verification code has been generated."}
    if not user:
        return success_msg

    reset_code = generate_6digit_code()
    user.verification_code = reset_code
    user.verification_expires_at = datetime.utcnow() + timedelta(minutes=15)
    db.commit()

    return {**success_msg, "reset_code_for_demo": reset_code}


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


# --- 3. PROFILE MANAGEMENT & AVATAR UPLOADS ---

@app.patch("/users/me", response_model=schemas.UserResponse)
def update_profile(
    profile_data: schemas.UserUpdateProfile,
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    if profile_data.username and profile_data.username != current_user.username:
        if db.query(models.User).filter(models.User.username == profile_data.username).first():
            raise HTTPException(status_code=400, detail="Username taken")
        current_user.username = profile_data.username

    db.commit()
    db.refresh(current_user)
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
            os.remove(old_file_path)

    profile_url = f"/static/profile_pics/{unique_filename}"
    current_user.profile_picture_url = profile_url
    
    db.commit()
    db.refresh(current_user)
    return {"profile_picture_url": profile_url}


# --- 4. PLAYLISTS (PUBLIC & PRIVATE) ---

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


@app.get("/users/{user_id}/public-profile", response_model=List[schemas.PlaylistResponse])
def get_public_user_playlists(user_id: UUID, db: Session = Depends(get_db)):
    return db.query(models.SavedPlaylist).filter(
        models.SavedPlaylist.user_id == user_id,
        models.SavedPlaylist.is_public == True
    ).all()


@app.patch("/playlists/{playlist_id}/visibility", response_model=schemas.PlaylistResponse)
def update_playlist_visibility(
    playlist_id: UUID,
    visibility: schemas.PlaylistUpdateVisibility,
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    playlist = db.query(models.SavedPlaylist).filter(
        models.SavedPlaylist.id == playlist_id,
        models.SavedPlaylist.user_id == current_user.id
    ).first()

    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")

    playlist.is_public = visibility.is_public
    db.commit()
    db.refresh(playlist)
    return playlist