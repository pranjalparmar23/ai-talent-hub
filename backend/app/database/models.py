from sqlalchemy import Column, String, Integer, DateTime, JSON, Text, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database.postgres import Base
import uuid


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String, unique=True, nullable=False, index=True)
    hashed_password = Column(String, nullable=False)
    role = Column(String, nullable=False, default="candidate")
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    resumes = relationship("Resume", back_populates="user", cascade="all, delete-orphan")
    interview_sessions = relationship(
        "InterviewSession", back_populates="user", cascade="all, delete-orphan"
    )


class Resume(Base):
    __tablename__ = "resumes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    blob_url = Column(String)
    parsed_data = Column(JSON)
    ats_score = Column(Integer)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    user = relationship("User", back_populates="resumes")


class JobDescription(Base):
    __tablename__ = "job_descriptions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    recruiter_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String)
    parsed_data = Column(JSON)
    raw_text = Column(Text)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)


class InterviewSession(Base):
    __tablename__ = "interview_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    company = Column(String)
    role = Column(String)
    history = Column(JSON, default=list)
    feedback = Column(JSON)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    user = relationship("User", back_populates="interview_sessions")