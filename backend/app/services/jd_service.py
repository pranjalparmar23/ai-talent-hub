"""JDService — handles JD text input, LLM parsing, DB persistence, retrieval.

JDs can be created from raw text (paste into UI) or from PDF upload (Phase 5).
For now we support the text path; PDF handling will reuse ResumeService's PDF
extractor when the recruiter flow lands.
"""
import logging

from fastapi import HTTPException, status
from sqlalchemy import select

from app.agents.recruiter.jd_analyzer import JDAnalyzerAgent
from app.database.models import JobDescription
from app.database.postgres import AsyncSessionLocal

logger = logging.getLogger(__name__)


class JDService:
    """Parse + persist job descriptions. Read-side helpers for the graph."""

    def __init__(self):
        self.analyzer = JDAnalyzerAgent()

    # ── Public API ─────────────────────────────────────────────────────────

    async def create_from_text(
        self,
        *,
        title: str,
        raw_text: str,
        user_id: str,
    ) -> dict:
        """Parse a JD from raw text and persist it.

        Args:
            title: User-provided title (e.g., "Senior Backend @ Amazon").
                   Falls back to the LLM-extracted title if the parsed one is better.
            raw_text: The full JD body text.
            user_id: The user creating the JD (candidate or recruiter).

        Returns:
            {"jd_id": UUID string, "parsed_data": dict, "parse_error": bool}
        """
        if not raw_text or not raw_text.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="JD text cannot be empty",
            )

        parsed = await self.analyzer.analyze(raw_text)

        # Prefer the user-supplied title; use LLM-extracted as fallback
        stored_title = title.strip() or parsed.get("title") or "Untitled JD"

        jd_id = await self._persist(
            user_id=user_id,
            title=stored_title,
            raw_text=raw_text,
            parsed_data=parsed,
        )

        return {
            "jd_id": jd_id,
            "parsed_data": parsed,
            "parse_error": bool(parsed.get("_parse_error")),
        }

    async def get_jd(self, jd_id: str, user_id: str) -> dict:
        """Fetch a JD by ID, enforcing owner-only access."""
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(JobDescription).where(JobDescription.id == jd_id))
            jd = result.scalar_one_or_none()

            if not jd:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="JD not found"
                )
            if str(jd.recruiter_id) != str(user_id):
                # Consistent with resume service — hide existence rather than 403
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="JD not found"
                )

            return {
                "jd_id": str(jd.id),
                "title": jd.title,
                "raw_text": jd.raw_text,
                "parsed_data": jd.parsed_data,
                "created_at": jd.created_at.isoformat(),
            }

    async def list_user_jds(self, user_id: str) -> list[dict]:
        """Return all JDs for a user, newest first. Dashboard view."""
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(JobDescription)
                .where(JobDescription.recruiter_id == user_id)
                .order_by(JobDescription.created_at.desc())
            )
            jds = result.scalars().all()
            return [
                {
                    "jd_id": str(jd.id),
                    "title": jd.title or "Untitled JD",
                    "created_at": jd.created_at.isoformat(),
                }
                for jd in jds
            ]

    # ── Internal ─────────────────────────────────────────────────────────

    async def _persist(
        self,
        *,
        user_id: str,
        title: str,
        raw_text: str,
        parsed_data: dict,
    ) -> str:
        """Insert a JobDescription row and return its UUID."""
        async with AsyncSessionLocal() as db:
            jd = JobDescription(
                recruiter_id=user_id,
                title=title,
                raw_text=raw_text,
                parsed_data=parsed_data,
            )
            db.add(jd)
            await db.commit()
            await db.refresh(jd)
            return str(jd.id)