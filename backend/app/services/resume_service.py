from fastapi import UploadFile
from app.agents.candidate.resume_parser import ResumeParserAgent
from app.database.postgres import AsyncSessionLocal
from app.database.models import Resume
from pathlib import Path
import pypdf
import io
import os

UPLOAD_DIR = Path("data/resumes")

class ResumeService:
    def __init__(self):
        self.parser = ResumeParserAgent()
        

    async def upload_and_parse(self, file: UploadFile, user_id: str) -> dict:
        content = await file.read()
        raw_text = self._extract_text(content)
        parsed = await self.parser.parse(raw_text)
        blob_url = await self._upload_to_blob(content, file.filename)
        async with AsyncSessionLocal() as db:
            resume = Resume(user_id=user_id, blob_url=blob_url, parsed_data=parsed)
            db.add(resume)
            await db.commit()
            return {"resume_id": str(resume.id), "parsed_data": parsed, "blob_url": blob_url}

    def _extract_text(self, content: bytes) -> str:
        reader = pypdf.PdfReader(io.BytesIO(content))
        return " ".join(page.extract_text() for page in reader.pages)

    async def _upload_to_blob(self, content: bytes, filename: str) -> str:
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

        file_path = UPLOAD_DIR / filename

        with open(file_path, "wb") as f:
            f.write(content)

        return str(file_path)
