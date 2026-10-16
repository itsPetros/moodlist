from datetime import datetime
from typing import List, Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict, EmailStr


# --- USER SCHEMAS ---

class UserResponse(BaseModel):
    id: UUID
    email: EmailStr
    username: str
    profile_picture_url: Optional[str] = None
    is_verified: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# --- PLAYLIST SCHEMAS ---

class PlaylistCreate(BaseModel):
    title: str
    mood: str
    genre: str
    weather: Optional[str] = None
    time_of_day: Optional[str] = None
    song_ids: List[str]
    is_public: bool = True


class PlaylistResponse(BaseModel):
    id: UUID
    user_id: UUID
    title: str
    mood: str
    genre: str
    weather: Optional[str] = None
    time_of_day: Optional[str] = None
    song_ids: List[str]
    is_public: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# --- TOKEN SCHEMA ---

class Token(BaseModel):
    access_token: str
    token_type: str