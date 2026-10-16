from datetime import datetime
import uuid
from database import Base
from sqlalchemy import ARRAY, Boolean, Column, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship


class User(Base):
    __tablename__ = "users"
    __table_args__ = {'extend_existing': True}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=True)  # Nullable for Google OAuth accounts
    profile_picture_url = Column(String(500), nullable=True)
    
    is_verified = Column(Boolean, default=True, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    playlists = relationship("SavedPlaylist", back_populates="owner", cascade="all, delete-orphan")


class SavedPlaylist(Base):
    __tablename__ = "saved_playlists"
    __table_args__ = {'extend_existing': True}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    title = Column(String(100), nullable=False)
    mood = Column(String(50), nullable=False)
    genre = Column(String(50), nullable=False)
    weather = Column(String(50), nullable=True)
    time_of_day = Column(String(50), nullable=True)
    song_ids = Column(ARRAY(Text), nullable=False)
    is_public = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    owner = relationship("User", back_populates="playlists")