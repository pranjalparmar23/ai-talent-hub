"""RecruiterService — JD intake and the data the recruiter pipeline reads.

Responsibilities:
  - Upload a JD file (PDF or plain text), validate it and hand it to JDService
    for parsing + persistence.
  - Load a JD for a recruiter (ownership-checked; other people's JDs are 404).
  - Load the candidate pool: the latest parsed resume of every candidate user,
    each with their most recent mock-interview score.

The scoring itself (matching, ranking, questions, recommendation) lives in the
agents and is orchestrated by RecruiterGraph; this service only reads and
validates data.

Interview score: only the candidate's most recent *completed* mock interview
counts (feedback generated, no parse error). It is a 0-10 number. Retakes
replace the old score, so one lucky or unlucky attempt can't stick forever.
"""

import asyncio
import io
import logging
import uuid
from typing import Any

import pypdf
from fastapi import HTTPException, UploadFile, status
from sqlalchemy import func, select

from app.database.models import InterviewSession, Resume, User
from app.database.postgres import AsyncSessionLocal
from app.services.jd_service import JDService

logger = logging.getLogger(__name__)


MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MIN_JD_CHARS = 50
MAX_POOL = 500
TEXT_EXTENSIONS = (".txt", ".md")


class RecruiterService:
    # NOTE: __init__ must stay argument-free — routes use Depends(RecruiterService),
    # and FastAPI would turn any extra parameter into a query-string field.
    def __init__(self):
        self.jd_service = JDService()

    # ── JD intake ──────────────────────────────────────────────────────────

    async def upload_jd(self, file: UploadFile, recruiter_id: str) -> dict:
        """Validate, extract text, parse and persist a JD.

        Returns {"jd_id", "parsed_data", "parse_error"}.
        """
        content = await file.read(MAX_UPLOAD_BYTES + 1)
        if not content:
            raise self._error(status.HTTP_400_BAD_REQUEST, "The uploaded file is empty")
        if len(content) > MAX_UPLOAD_BYTES:
            raise self._error(
                413,
                f"File is too large (limit {MAX_UPLOAD_BYTES // (1024 * 1024)} MB)",
            )

        raw_text = await asyncio.to_thread(
            self._extract_text, content, file.filename, file.content_type
        )
        if len(raw_text.strip()) < MIN_JD_CHARS:
            raise self._error(
                422,
                "Could not find enough text in this file. "
                "If it is a scanned PDF, upload a text-based copy instead.",
            )

        # Empty title → JDService falls back to the title the LLM extracts.
        return await self.jd_service.create_from_text(
            title="", raw_text=raw_text, user_id=recruiter_id
        )

    async def get_jd_data(self, jd_id: str, recruiter_id: str) -> dict:
        """Return {"jd_id", "title", "parsed_data"} for a JD this recruiter owns.

        404 for unknown or foreign JDs; 422 if the JD was never analysed (the
        pipeline would silently score everyone 0 against an empty skill list).
        """
        if not self._is_uuid(jd_id):
            raise self._error(status.HTTP_404_NOT_FOUND, "JD not found")
        jd = await self.jd_service.get_jd(jd_id, recruiter_id)

        parsed = jd.get("parsed_data")
        if not isinstance(parsed, dict) or not parsed or parsed.get("_parse_error"):
            raise self._error(
                422,
                "This JD could not be analysed. Please upload it again.",
            )
        return {
            "jd_id": jd["jd_id"],
            "title": jd.get("title") or "Untitled JD",
            "parsed_data": parsed,
        }

    # ── Candidate data ─────────────────────────────────────────────────────

    async def load_candidate_pool(self, limit: int = MAX_POOL) -> list[dict]:
        """Latest parsed resume of every candidate user, newest first.

        Each item: {"candidate_id", "resume_id", "name", "resume_data",
        "interview_score"}. Users without a parsed resume are skipped.
        """
        limit = max(1, min(int(limit), MAX_POOL))
        rows = await self._pool_rows(limit)
        rows = [r for r in rows if isinstance(r.get("resume_data"), dict)]
        scores = await self._interview_scores([r["candidate_id"] for r in rows])
        return [self._candidate(r, scores.get(r["candidate_id"])) for r in rows]

    async def load_candidate(self, candidate_id: str) -> dict:
        """One candidate (latest resume + interview score), or 404."""
        if not self._is_uuid(candidate_id):
            raise self._error(status.HTTP_404_NOT_FOUND, "Candidate not found")
        row = await self._resume_row(uuid.UUID(candidate_id))
        if not row or not isinstance(row.get("resume_data"), dict):
            raise self._error(status.HTTP_404_NOT_FOUND, "Candidate not found")
        scores = await self._interview_scores([row["candidate_id"]])
        return self._candidate(row, scores.get(row["candidate_id"]))

    async def _interview_scores(self, candidate_ids: list[str]) -> dict[str, float]:
        """candidate_id → latest usable overall score (0-10). Missing = no entry."""
        if not candidate_ids:
            return {}
        rows = await self._interview_rows([uuid.UUID(c) for c in candidate_ids])
        scores: dict[str, float] = {}
        for row in rows:  # newest first, so the first usable one per user wins
            user_id = row["user_id"]
            if user_id in scores:
                continue
            score = self._score_from_feedback(row.get("feedback"))
            if score is not None:
                scores[user_id] = score
        return scores

    # ── Thin database methods (replaced by fakes in unit tests) ────────────

    async def _pool_rows(self, limit: int) -> list[dict[str, Any]]:
        latest = (
            select(Resume.user_id, func.max(Resume.created_at).label("latest"))
            .group_by(Resume.user_id)
            .subquery()
        )
        query = (
            select(Resume)
            .join(
                latest,
                (Resume.user_id == latest.c.user_id)
                & (Resume.created_at == latest.c.latest),
            )
            .join(User, User.id == Resume.user_id)
            .where(User.role == "candidate")
            .order_by(Resume.created_at.desc())
            .limit(limit * 2)  # headroom: ties on created_at are de-duplicated below
        )
        async with AsyncSessionLocal() as db:
            resumes = (await db.execute(query)).scalars().all()

        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for resume in resumes:
            user_id = str(resume.user_id)
            if user_id in seen:
                continue
            seen.add(user_id)
            rows.append(self._row(resume))
            if len(rows) >= limit:
                break
        return rows

    async def _resume_row(self, candidate_id: uuid.UUID) -> dict[str, Any] | None:
        query = (
            select(Resume)
            .join(User, User.id == Resume.user_id)
            .where(Resume.user_id == candidate_id, User.role == "candidate")
            .order_by(Resume.created_at.desc())
            .limit(1)
        )
        async with AsyncSessionLocal() as db:
            resume = (await db.execute(query)).scalars().first()
        return self._row(resume) if resume else None

    async def _interview_rows(self, user_ids: list[uuid.UUID]) -> list[dict[str, Any]]:
        query = (
            select(InterviewSession.user_id, InterviewSession.feedback)
            .where(
                InterviewSession.user_id.in_(user_ids),
                InterviewSession.feedback.is_not(None),
            )
            .order_by(InterviewSession.created_at.desc())
        )
        async with AsyncSessionLocal() as db:
            result = await db.execute(query)
            return [
                {"user_id": str(user_id), "feedback": feedback}
                for user_id, feedback in result.all()
            ]

    # ── Pure helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _row(resume: Resume) -> dict[str, Any]:
        return {
            "candidate_id": str(resume.user_id),
            "resume_id": str(resume.id),
            "resume_data": resume.parsed_data,
        }

    @staticmethod
    def _candidate(row: dict[str, Any], interview_score: float | None) -> dict:
        resume_data = row["resume_data"]
        name = resume_data.get("name")
        return {
            "candidate_id": row["candidate_id"],
            "resume_id": row.get("resume_id"),
            "name": name.strip() if isinstance(name, str) and name.strip() else None,
            "resume_data": resume_data,
            "interview_score": interview_score,
        }

    @staticmethod
    def _score_from_feedback(feedback: Any) -> float | None:
        if not isinstance(feedback, dict) or feedback.get("_parse_error"):
            return None
        score = feedback.get("overall_score")
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            return None
        if score != score or not 0 <= score <= 10:  # NaN or out of range
            return None
        return float(score)

    @classmethod
    def _extract_text(
        cls, content: bytes, filename: str | None, content_type: str | None
    ) -> str:
        name = (filename or "").lower()
        if content.startswith(b"%PDF"):
            return cls._pdf_text(content)
        if name.endswith(TEXT_EXTENSIONS) or (content_type or "").startswith("text/"):
            return content.decode("utf-8", errors="ignore").replace("\x00", "")
        raise cls._error(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "Unsupported file type. Upload a PDF or a .txt file.",
        )

    @classmethod
    def _pdf_text(cls, content: bytes) -> str:
        try:
            reader = pypdf.PdfReader(io.BytesIO(content))
            if reader.is_encrypted and not reader.decrypt(""):
                raise ValueError("password protected")
            pages = [page.extract_text() or "" for page in reader.pages]
        except Exception as exc:
            logger.warning("JD PDF could not be read: %s", exc)
            raise cls._error(
                422,
                "Could not read this PDF. It may be corrupted or password protected.",
            ) from exc
        return "\n".join(pages).replace("\x00", "")

    @staticmethod
    def _is_uuid(value: str) -> bool:
        try:
            uuid.UUID(str(value))
            return True
        except ValueError:
            return False

    @staticmethod
    def _error(status_code: int, detail: str) -> HTTPException:
        return HTTPException(status_code=status_code, detail=detail)
