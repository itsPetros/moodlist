import re
from datetime import datetime
from typing import List, Optional
from uuid import UUID
from pydantic import BaseModel, EmailStr, Field, validator

def validate_password_complexity(value: str) -> str:
    if len(value) < 8:
        raise ValueError("Password must be at least 8 characters long.")
    if not re.search(r"[A-Z]", value):
        raise ValueError("Password must contain at least one uppercase letter.")
    if not re.search(r"[a-z]", value):
        raise ValueError("Password must contain at least one lowercase letter.")
    if not re.search(r"\d", value):
        raise ValueError("Password must contain at least one number.")
    if not re.search(r"[!@#$%^&*()_+\-=\[\]{};':\"\\|,.<>/?]", value):
        raise ValueError("Password must contain at least one special character.")
    return value


class UserRegister(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    email: EmailStr

class SetPasswordRequest(BaseModel):
    email: EmailStr
    code: str = Field(..., min_length=6, max_length=6)
    password: str

    @validator("password")
    def password_check(cls, v):
        return validate_password_complexity(v)

class ForgotPasswordRequest(BaseModel):
    email: EmailStr

class ResetPasswordRequest(BaseModel):
    email: EmailStr
    code: str = Field(..., min_length=6, max_length=6)
    new_password: str

    @validator("new_password")
    def new_password_check(cls, v):
        return validate_password_complexity(v)

class UserUpdateProfile(BaseModel):
    username: Optional[str] = Field(None, min_length=3, max_length=50)

class UserResponse(BaseModel):
    id: UUID
    username: str
    email: str
    profile_picture_url: Optional[str] = None
    is_verified: bool
    created_at: datetime

    class Config:
        from_attributes = True

class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    username: Optional[str] = None

class PlaylistCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=100)
    mood: str
    genre: str
    weather: Optional[str] = None
    time_of_day: Optional[str] = None
    song_ids: List[str]
    is_public: bool = True

class PlaylistUpdateVisibility(BaseModel):
    is_public: bool

class PlaylistResponse(BaseModel):
    id: UUID
    title: str
    mood: str
    genre: str
    weather: Optional[str]
    time_of_day: Optional[str]
    song_ids: List[str]
    is_public: bool
    created_at: datetime

    class Config:
        from_attributes = True