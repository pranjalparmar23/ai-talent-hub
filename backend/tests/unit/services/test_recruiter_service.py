"""Unit tests for RecruiterService.

JDService and the three thin database methods are replaced with in-memory
fakes, so these tests cover the validation, ownership and scoring-rule logic.
The SQL itself is exercised by the integration test against Postgres.
"""

import io
import uuid

import pytest
from fastapi import HTTPException, UploadFile
from pypdf import PdfWriter
from starlette.datastructures import Headers

from app.services.recruiter_service import (
    MAX_POOL,
    MAX_UPLOAD_BYTES,
    RecruiterService,
)

RECRUITER_ID = str(uuid.uuid4())
OTHER_RECRUITER_ID = str(uuid.uuid4())
JD_TEXT = "Senior Backend Engineer. We need Python, FastAPI and PostgreSQL skills."


def make_pdf(text: str) -> bytes:
    """A minimal one-page PDF whose page text is `text` (empty = no text)."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode() if text else b""
    objects = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R"
        b"/Resources<</Font<</F1 5 0 R>>>>>>",
        b"<</Length %d>>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref_at = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    return out + b"trailer<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF" % (
        len(objects) + 1,
        xref_at,
    )


def encrypted_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.encrypt("secret")
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def upload(content: bytes, filename: str, content_type: str = "") -> UploadFile:
    return UploadFile(
        file=io.BytesIO(content),
        filename=filename,
        headers=Headers({"content-type": content_type}),
    )


class FakeJDService:
    def __init__(self):
        self.created: list[dict] = []
        self.jd: dict | None = None  # what get_jd returns
        self.calls: list[tuple] = []

    async def create_from_text(self, *, title, raw_text, user_id):
        self.created.append({"title": title, "raw_text": raw_text, "user": user_id})
        return {"jd_id": "jd-1", "parsed_data": {"title": "T"}, "parse_error": False}

    async def get_jd(self, jd_id, user_id):
        self.calls.append((jd_id, user_id))
        if self.jd is None or self.jd.get("owner") != user_id:
            raise HTTPException(status_code=404, detail="JD not found")
        return self.jd


def resume_row(user_id=None, data=None, resume_id=None):
    return {
        "candidate_id": user_id or str(uuid.uuid4()),
        "resume_id": resume_id or str(uuid.uuid4()),
        "resume_data": {"name": "Ada", "skills": ["Python"]} if data is None else data,
    }


@pytest.fixture
def svc(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    service = RecruiterService()
    service.jd_service = FakeJDService()
    service.pool: list[dict] = []
    service.sessions: list[dict] = []  # newest first, like the real query
    service.pool_limits: list[int] = []

    async def pool_rows(limit):
        service.pool_limits.append(limit)
        return service.pool[:limit]

    async def resume_row_for(candidate_id):
        for row in service.pool:
            if row["candidate_id"] == str(candidate_id):
                return row
        return None

    async def interview_rows(user_ids):
        wanted = {str(u) for u in user_ids}
        return [s for s in service.sessions if s["user_id"] in wanted]

    service._pool_rows = pool_rows
    service._resume_row = resume_row_for
    service._interview_rows = interview_rows
    return service


async def status_of(coro) -> int:
    with pytest.raises(HTTPException) as caught:
        await coro
    return caught.value.status_code


# ── JD upload ──────────────────────────────────────────────────────────────


class TestUploadJd:
    async def test_text_file_is_parsed_and_persisted(self, svc):
        result = await svc.upload_jd(
            upload(JD_TEXT.encode(), "jd.txt", "text/plain"), RECRUITER_ID
        )
        assert result["jd_id"] == "jd-1"
        created = svc.jd_service.created[0]
        assert created["raw_text"] == JD_TEXT
        assert created["user"] == RECRUITER_ID
        assert created["title"] == ""  # lets the analyzer's title win

    async def test_pdf_text_is_extracted(self, svc):
        pdf = make_pdf("Backend Engineer needing Python and Postgres and Docker skills")
        await svc.upload_jd(upload(pdf, "jd.pdf", "application/pdf"), RECRUITER_ID)
        assert "Backend Engineer" in svc.jd_service.created[0]["raw_text"]

    async def test_pdf_detected_by_content_not_filename(self, svc):
        pdf = make_pdf("Backend Engineer needing Python and Postgres and Docker skills")
        await svc.upload_jd(upload(pdf, "job-post", ""), RECRUITER_ID)
        assert svc.jd_service.created

    async def test_markdown_extension_accepted(self, svc):
        await svc.upload_jd(upload(JD_TEXT.encode(), "jd.md"), RECRUITER_ID)
        assert svc.jd_service.created

    async def test_null_bytes_stripped(self, svc):
        await svc.upload_jd(
            upload(JD_TEXT.encode().replace(b" ", b"\x00 "), "jd.txt"), RECRUITER_ID
        )
        assert "\x00" not in svc.jd_service.created[0]["raw_text"]

    async def test_empty_file_is_400(self, svc):
        assert await status_of(svc.upload_jd(upload(b"", "e.txt"), RECRUITER_ID)) == 400

    async def test_oversized_file_is_413(self, svc):
        big = upload(b"a" * (MAX_UPLOAD_BYTES + 1), "big.txt", "text/plain")
        assert await status_of(svc.upload_jd(big, RECRUITER_ID)) == 413

    async def test_exactly_max_size_is_allowed(self, svc):
        body = b"Backend role. " * (MAX_UPLOAD_BYTES // 14)
        body = body[:MAX_UPLOAD_BYTES]
        await svc.upload_jd(upload(body, "max.txt", "text/plain"), RECRUITER_ID)
        assert svc.jd_service.created

    async def test_unsupported_type_is_415(self, svc):
        docx = upload(b"PK\x03\x04 zipped docx", "jd.docx")
        assert await status_of(svc.upload_jd(docx, RECRUITER_ID)) == 415

    async def test_too_little_text_is_422(self, svc):
        short = upload(b"too short", "s.txt", "text/plain")
        assert await status_of(svc.upload_jd(short, RECRUITER_ID)) == 422
        assert svc.jd_service.created == []

    async def test_scanned_pdf_without_text_is_422(self, svc):
        scanned = upload(make_pdf(""), "scan.pdf", "application/pdf")
        assert await status_of(svc.upload_jd(scanned, RECRUITER_ID)) == 422

    async def test_corrupt_pdf_is_422(self, svc):
        bad = upload(b"%PDF-1.4 this is not really a pdf", "bad.pdf")
        assert await status_of(svc.upload_jd(bad, RECRUITER_ID)) == 422

    async def test_password_protected_pdf_is_422(self, svc):
        locked = upload(encrypted_pdf(), "locked.pdf", "application/pdf")
        assert await status_of(svc.upload_jd(locked, RECRUITER_ID)) == 422


# ── get_jd_data ────────────────────────────────────────────────────────────


class TestGetJdData:
    def _jd(self, svc, owner=RECRUITER_ID, title="Backend"):
        jd_id = str(uuid.uuid4())
        svc.jd_service.jd = {
            "owner": owner,
            "jd_id": jd_id,
            "title": title,
            "parsed_data": {"skills_required": ["Python"]},
        }
        return jd_id

    async def test_returns_owned_jd(self, svc):
        jd_id = self._jd(svc)
        data = await svc.get_jd_data(jd_id, RECRUITER_ID)
        assert data == {
            "jd_id": jd_id,
            "title": "Backend",
            "parsed_data": {"skills_required": ["Python"]},
        }

    async def test_other_recruiters_jd_is_404(self, svc):
        jd_id = self._jd(svc, owner=OTHER_RECRUITER_ID)
        assert await status_of(svc.get_jd_data(jd_id, RECRUITER_ID)) == 404

    async def test_malformed_id_is_404_without_touching_the_db(self, svc):
        assert await status_of(svc.get_jd_data("not-a-uuid", RECRUITER_ID)) == 404
        assert svc.jd_service.calls == []

    @pytest.mark.parametrize("parsed", [{"_parse_error": True}, {}])
    async def test_unanalysed_jd_is_422(self, svc, parsed):
        jd_id = self._jd(svc)
        svc.jd_service.jd["parsed_data"] = parsed
        assert await status_of(svc.get_jd_data(jd_id, RECRUITER_ID)) == 422

    async def test_jd_without_parsed_data_is_422(self, svc):
        jd_id = self._jd(svc)
        svc.jd_service.jd["parsed_data"] = None
        assert await status_of(svc.get_jd_data(jd_id, RECRUITER_ID)) == 422

    async def test_missing_title_gets_default(self, svc):
        jd_id = self._jd(svc, title=None)
        assert (await svc.get_jd_data(jd_id, RECRUITER_ID))["title"] == "Untitled JD"


# ── Candidate pool ─────────────────────────────────────────────────────────


def session(user_id, feedback):
    return {"user_id": user_id, "feedback": feedback}


class TestCandidatePool:
    async def test_shape_and_name_cleanup(self, svc):
        row = resume_row(data={"name": "  Ada Lovelace ", "skills": ["Python"]})
        svc.pool = [row]
        [candidate] = await svc.load_candidate_pool()
        assert candidate == {
            "candidate_id": row["candidate_id"],
            "resume_id": row["resume_id"],
            "name": "Ada Lovelace",
            "resume_data": {"name": "  Ada Lovelace ", "skills": ["Python"]},
            "interview_score": None,
        }

    @pytest.mark.parametrize("name", [None, "", "   ", 42])
    async def test_missing_or_bad_name_is_none(self, svc, name):
        svc.pool = [resume_row(data={"name": name})]
        assert (await svc.load_candidate_pool())[0]["name"] is None

    async def test_rows_without_parsed_resume_are_skipped(self, svc):
        good = resume_row()
        svc.pool = [{**resume_row(), "resume_data": None}, good]
        pool = await svc.load_candidate_pool()
        assert [c["candidate_id"] for c in pool] == [good["candidate_id"]]

    async def test_empty_pool(self, svc):
        assert await svc.load_candidate_pool() == []

    async def test_limit_is_clamped(self, svc):
        await svc.load_candidate_pool(limit=10_000)
        await svc.load_candidate_pool(limit=0)
        assert svc.pool_limits == [MAX_POOL, 1]

    async def test_newest_usable_interview_score_wins(self, svc):
        row = resume_row()
        svc.pool = [row]
        uid = row["candidate_id"]
        svc.sessions = [  # newest first
            session(uid, {"overall_score": 9.0, "_parse_error": True}),  # unusable
            session(uid, {"overall_score": 7.5}),  # newest usable -> wins
            session(uid, {"overall_score": 3.0}),  # older, ignored
        ]
        assert (await svc.load_candidate_pool())[0]["interview_score"] == 7.5

    async def test_scores_are_per_candidate(self, svc):
        a, b = resume_row(), resume_row()
        svc.pool = [a, b]
        svc.sessions = [
            session(a["candidate_id"], {"overall_score": 6}),
            session(b["candidate_id"], {"overall_score": 8.2}),
        ]
        scores = {
            c["candidate_id"]: c["interview_score"]
            for c in await svc.load_candidate_pool()
        }
        assert scores == {a["candidate_id"]: 6.0, b["candidate_id"]: 8.2}

    @pytest.mark.parametrize(
        "feedback",
        [
            None,
            "text",
            {},
            {"overall_score": "8"},
            {"overall_score": True},
            {"overall_score": 11},
            {"overall_score": -1},
            {"overall_score": float("nan")},
            {"overall_score": 5, "_parse_error": True},
        ],
    )
    async def test_unusable_feedback_gives_no_score(self, svc, feedback):
        row = resume_row()
        svc.pool = [row]
        svc.sessions = [session(row["candidate_id"], feedback)]
        assert (await svc.load_candidate_pool())[0]["interview_score"] is None

    @pytest.mark.parametrize("score", [0, 10, 0.0, 4.5])
    async def test_boundary_scores_are_valid(self, svc, score):
        row = resume_row()
        svc.pool = [row]
        svc.sessions = [session(row["candidate_id"], {"overall_score": score})]
        got = (await svc.load_candidate_pool())[0]["interview_score"]
        assert got == float(score)


class TestLoadCandidate:
    async def test_found_with_score(self, svc):
        row = resume_row()
        svc.pool = [row]
        svc.sessions = [session(row["candidate_id"], {"overall_score": 8})]
        candidate = await svc.load_candidate(row["candidate_id"])
        assert candidate["interview_score"] == 8.0
        assert candidate["name"] == "Ada"

    async def test_malformed_id_is_404(self, svc):
        assert await status_of(svc.load_candidate("nope")) == 404

    async def test_unknown_candidate_is_404(self, svc):
        assert await status_of(svc.load_candidate(str(uuid.uuid4()))) == 404

    async def test_unparsed_resume_is_404(self, svc):
        row = {**resume_row(), "resume_data": None}
        svc.pool = [row]
        assert await status_of(svc.load_candidate(row["candidate_id"])) == 404
