"""ResumeParserAgent — extracts structured JSON from raw resume text using Groq."""
import logging
from langchain_core.prompts import ChatPromptTemplate

from app.llm.models import get_llm
from app.llm.prompts.resume_prompts import RESUME_PARSE_PROMPT
from app.utils.json_parser import parse_llm_json

logger = logging.getLogger(__name__)


# Fallback returned when the LLM output is unparseable — keeps the pipeline moving
# and gives the frontend a structured error to display.
_PARSE_FALLBACK = {
    "name": None,
    "email": None,
    "phone": None,
    "location": None,
    "linkedin": None,
    "github": None,
    "summary": None,
    "skills": [],
    "experience_years": 0,
    "experience": [],
    "education": [],
    "projects": [],
    "certifications": [],
    "_parse_error": True,
}


class ResumeParserAgent:
    """Turns raw resume text into structured JSON via Groq.

    Usage:
        agent = ResumeParserAgent()
        parsed = await agent.parse(raw_pdf_text)
        # parsed["skills"], parsed["experience_years"], etc.
    """

    def __init__(self):
        self.llm = get_llm(temperature=0)
        self.prompt = ChatPromptTemplate.from_template(RESUME_PARSE_PROMPT)
        self.chain = self.prompt | self.llm

    async def parse(self, raw_text: str) -> dict:
        """Parse resume text into structured JSON.

        Args:
            raw_text: Extracted PDF text from pypdf.

        Returns:
            Dict matching the schema in resume_prompts.py. On LLM/parse failure,
            returns the fallback dict with `_parse_error: True` set — never raises.
        """
        if not raw_text or not raw_text.strip():
            logger.warning("ResumeParserAgent called with empty text")
            return {**_PARSE_FALLBACK, "_parse_error": True, "_error_reason": "empty_input"}

        # Guardrail — if the PDF has a huge amount of text (>50k chars), truncate.
        # Real resumes are typically <10k chars. Anything larger is scanned OCR noise
        # or a PDF with embedded images that pypdf mishandled.
        if len(raw_text) > 50_000:
            logger.warning(f"Resume text truncated from {len(raw_text)} to 50000 chars")
            raw_text = raw_text[:50_000]

        try:
            response = await self.chain.ainvoke({"resume_text": raw_text})
        except Exception as e:
            logger.exception("Groq call failed in ResumeParserAgent")
            return {**_PARSE_FALLBACK, "_parse_error": True, "_error_reason": str(e)[:200]}

        return parse_llm_json(
            response.content,
            fallback={**_PARSE_FALLBACK, "_parse_error": True, "_raw_output": response.content[:500]},
        )