"""ResumeService — handles PDF upload, text extraction, LLM parsing, DB persistence."""
import io
import logging
import os
import uuid
from pathlib import Path
from typing import Optional

import pypdf
from fastapi import HTTPException, UploadFile, status
from sqlalchemy import select

from app.agents.candidate.resume_parser import ResumeParserAgent
from app.database.models import Resume
from app.database.postgres import AsyncSessionLocal

logger = logging.getLogger(__name__)


# Storage config (env-driven, falls back to project's data/uploads/resumes/)
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "./data/uploads")) / "resumes"
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB


class ResumeService:
    """Orchestrates: validate → save PDF → extract text → parse → persist to Postgres."""

    def __init__(self):
        self.parser = ResumeParserAgent()

    # ── Public API ─────────────────────────────────────────────────────────

    async def upload_and_parse(self, file: UploadFile, user_id: str) -> dict:
        """Full upload flow: save PDF, parse it with LLM, persist result.

        Returns:
            {
              "resume_id": UUID string,
              "parsed_data": dict (from ResumeParserAgent),
              "file_path": local path,
              "parse_error": bool
            }
        """
        # 1. Validate the upload
        content = await self._read_and_validate(file)

        # 2. Save PDF to disk with UUID name (avoids filename collisions)
        stored_filename = f"{uuid.uuid4()}.pdf"
        file_path = await self._save_to_disk(content, stored_filename)
        logger.info(f"Resume saved: {file_path}")

        # 3. Extract raw text
        raw_text = self._extract_text(content)
        if not raw_text.strip():
            logger.warning(f"No text extracted from resume {stored_filename}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Could not extract text from PDF. Is it a scanned image? "
                "Try uploading a text-based PDF.",
            )

        # 4. LLM-parse into structured JSON (never raises — has fallback)
        parsed = await self.parser.parse(raw_text)

        # 5. Persist to Postgres
        resume_id = await self._persist(
            user_id=user_id,
            file_path=str(file_path),
            parsed_data=parsed,
        )

        return {
            "resume_id": resume_id,
            "parsed_data": parsed,
            "file_path": str(file_path),
            "parse_error": bool(parsed.get("_parse_error")),
        }

    async def get_resume(self, resume_id: str, user_id: str) -> dict:
        """Fetch a resume by ID, ensuring it belongs to the requesting user."""
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Resume).where(Resume.id == resume_id))
            resume = result.scalar_one_or_none()

            if not resume:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="Resume not found"
                )
            if str(resume.user_id) != str(user_id):
                # Don't leak existence of resumes owned by other users
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="Resume not found"
                )

            return {
                "resume_id": str(resume.id),
                "parsed_data": resume.parsed_data,
                "file_path": resume.blob_url,
                "ats_score": resume.ats_score,
                "created_at": resume.created_at.isoformat(),
            }

    async def list_user_resumes(self, user_id: str) -> list[dict]:
        """Return all resumes for a user, newest first. Used by dashboard."""
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Resume).where(Resume.user_id == user_id).order_by(Resume.created_at.desc())
            )
            resumes = result.scalars().all()
            return [
                {
                    "resume_id": str(r.id),
                    "name": (r.parsed_data or {}).get("name") or "Unnamed",
                    "ats_score": r.ats_score,
                    "created_at": r.created_at.isoformat(),
                }
                for r in resumes
            ]

    # ── Internal helpers ───────────────────────────────────────────────────

    async def _read_and_validate(self, file: UploadFile) -> bytes:
        """Read the upload, enforce size + content-type rules."""
        if file.content_type != "application/pdf":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only PDF files are accepted",
            )

        content = await file.read()
        if len(content) == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty"
            )
        if len(content) > MAX_FILE_SIZE:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File exceeds {MAX_FILE_SIZE // (1024 * 1024)} MB limit",
            )
        return content

    async def _save_to_disk(self, content: bytes, filename: str) -> Path:
        """Persist bytes under UPLOAD_DIR, creating the directory if missing."""
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        path = UPLOAD_DIR / filename
        with open(path, "wb") as f:
            f.write(content)
        return path

    def _extract_text(self, content: bytes) -> str:
        """Extract text from all pages of the PDF."""
        try:
            reader = pypdf.PdfReader(io.BytesIO(content))
        except Exception as e:
            logger.exception("pypdf failed to read PDF")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Could not read PDF: {e}",
            )

        pages = []
        for i, page in enumerate(reader.pages):
            try:
                text = page.extract_text() or ""
            except Exception as e:
                logger.warning(f"Failed to extract page {i}: {e}")
                text = ""
            pages.append(text)

        return "\n\n".join(pages).strip()

    async def _persist(self, user_id: str, file_path: str, parsed_data: dict) -> str:
        """Insert a Resume row and return its UUID."""
        async with AsyncSessionLocal() as db:
            resume = Resume(
                user_id=user_id,
                blob_url=file_path,
                parsed_data=parsed_data,
            )
            db.add(resume)
            await db.commit()
            await db.refresh(resume)
            return str(resume.id)