from fastapi import UploadFile
from app.agents.recruiter.jd_analyzer import JDAnalyzerAgent
from app.database.postgres import AsyncSessionLocal
from app.database.models import JobDescription
import pypdf
import io


class RecruiterService:
    def __init__(self):
        self.analyzer = JDAnalyzerAgent()

    async def upload_jd(self, file: UploadFile, recruiter_id: str) -> dict:
        content = await file.read()
        raw_text = self._extract_text(content)
        parsed = await self.analyzer.analyze(raw_text)
        async with AsyncSessionLocal() as db:
            jd = JobDescription(recruiter_id=recruiter_id, raw_text=raw_text,
                                parsed_data=parsed, title=parsed.get("title", ""))
            db.add(jd)
            await db.commit()
            return {"jd_id": str(jd.id), "parsed_data": parsed}

    def _extract_text(self, content: bytes) -> str:
        try:
            reader = pypdf.PdfReader(io.BytesIO(content))
            return " ".join(page.extract_text() for page in reader.pages)
        except Exception:
            return content.decode("utf-8", errors="ignore")
