"""Song feedback (thumbs up / thumbs down) and the helpers that stop playlists repeating.

- A thumbs-down means "never show me this song again". A thumbs-up makes a song likelier.
- Songs given to the person in their last few playlists are avoided while there are
  enough other songs to choose from.

Everything is keyed by the song (artist + title), not by the YouTube video, so feedback
stays valid if a video is later replaced.
"""
import logging
import os
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import Column, DateTime, ForeignKey, SmallInteger, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

import auth
import catalog
import models
from database import Base, get_db

logger = logging.getLogger("moodlist")

RECENT_PLAYLISTS = 5          # songs from this many latest playlists are avoided
MAX_FEEDBACK_PER_USER = 2000  # stops one account from growing the table without limit
RATE_PER_MIN = int(os.getenv("FEEDBACK_RATE_PER_MIN", "120"))


def _now():
    # Naive UTC, like the other timestamp columns in this project.
    return datetime.now(timezone.utc).replace(tzinfo=None)


class SongFeedback(Base):
    __tablename__ = "song_feedback"
    __table_args__ = (UniqueConstraint("user_id", "song_key", name="uq_song_feedback_user_song"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    song_key = Column(String(300), nullable=False)
    video_id = Column(String(20), nullable=True)
    value = Column(SmallInteger, nullable=False)  # 1 = liked, -1 = disliked
    created_at = Column(DateTime, default=_now, nullable=False)
    updated_at = Column(DateTime, default=_now, onupdate=_now, nullable=False)


class FeedbackIn(BaseModel):
    video_id: str = Field(pattern=r"^[A-Za-z0-9_-]{6,20}$")
    value: int = Field(ge=-1, le=1)  # 1 like, -1 dislike, 0 take the feedback back


class FeedbackOut(BaseModel):
    video_id: str
    value: int


# --------------------------------------------------------------------- small rate limiter

_hits = defaultdict(deque)


def _limited(key: str) -> bool:
    now = time.monotonic()
    q = _hits[key]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= RATE_PER_MIN:
        return True
    q.append(now)
    return False


# ----------------------------------------------------------------------------- endpoints

router = APIRouter()


def _store(db: Session, user: models.User, key: str, video_id: str, value: int) -> None:
    row = (
        db.query(SongFeedback)
        .filter(SongFeedback.user_id == user.id, SongFeedback.song_key == key)
        .first()
    )
    if value == 0:
        if row:
            db.delete(row)
    elif row:
        row.value = value
        row.video_id = video_id
    else:
        count = db.query(func.count(SongFeedback.id)).filter(SongFeedback.user_id == user.id).scalar()
        if count >= MAX_FEEDBACK_PER_USER:
            raise HTTPException(status_code=400, detail="You have reached the feedback limit.")
        db.add(SongFeedback(user_id=user.id, song_key=key, video_id=video_id, value=value))
    db.commit()


@router.post("/api/feedback", response_model=FeedbackOut)
def set_feedback(
    body: FeedbackIn,
    user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    if _limited(str(user.id)):
        raise HTTPException(status_code=429, detail="Too many requests. Please slow down a little.")

    song = catalog.find_song_by_video(body.video_id)
    if song is None:
        raise HTTPException(status_code=404, detail="Unknown song.")
    key = catalog.song_key(song["artist"], song["title"])

    try:
        try:
            _store(db, user, key, body.video_id, body.value)
        except IntegrityError:
            # Two quick taps can race to insert the same row; the second one just updates it.
            db.rollback()
            _store(db, user, key, body.video_id, body.value)
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Could not save song feedback")
        raise HTTPException(status_code=503, detail="Could not save that right now. Please try again.")
    return FeedbackOut(video_id=body.video_id, value=body.value)


@router.delete("/api/feedback")
def clear_feedback(
    value: Optional[int] = Query(None, description="-1 clears dislikes, 1 clears likes, empty clears both"),
    user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    """Let a person undo feedback in bulk, for example if a thumbs-down was a mistake."""
    if value not in (None, -1, 1):
        raise HTTPException(status_code=422, detail="value must be -1, 1 or empty")
    try:
        q = db.query(SongFeedback).filter(SongFeedback.user_id == user.id)
        if value is not None:
            q = q.filter(SongFeedback.value == value)
        removed = q.delete(synchronize_session=False)
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Could not clear song feedback")
        raise HTTPException(status_code=503, detail="Could not do that right now. Please try again.")
    return {"removed": removed}


# ---------------------------------------------------------------- helpers for the picker

def feedback_sets(db: Session, user: models.User):
    """(liked, blocked) as sets of song keys. Falls back to empty sets if the database
    hiccups, so a playlist can still be made."""
    try:
        rows = (
            db.query(SongFeedback.song_key, SongFeedback.value)
            .filter(SongFeedback.user_id == user.id)
            .all()
        )
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Could not read song feedback")
        return set(), set()
    return {k for k, v in rows if v > 0}, {k for k, v in rows if v < 0}


def recent_song_keys(db: Session, user: models.User, playlists: int = RECENT_PLAYLISTS):
    """Song keys from the person's last few generated playlists."""
    try:
        rows = (
            db.query(models.PlaylistHistory.tracks)
            .filter(models.PlaylistHistory.user_id == user.id)
            .order_by(models.PlaylistHistory.created_at.desc())
            .limit(playlists)
            .all()
        )
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Could not read playlist history")
        return set()
    keys = set()
    for (tracks,) in rows:
        for t in tracks or []:
            keys.add(catalog.song_key(t.get("artist", ""), t.get("title", "")))
    return keys
