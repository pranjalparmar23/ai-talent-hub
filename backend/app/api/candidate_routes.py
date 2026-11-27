"""Candidate-facing API endpoints.

All routes require JWT auth via get_current_user (returns dict with id/email/role).
Route handlers stay thin — they validate input, load DB rows, delegate to services
or graphs, and return typed responses.
"""

from fastapi import (
    APIRouter,
    Depends,
    File,
    Header,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)

from app.database.schemas import (
    ATSScoreResponse,
    InterviewFeedbackResponse,
    InterviewRespondRequest,
    InterviewRespondResponse,
    InterviewSessionResponse,
    InterviewStartRequest,
    InterviewStartResponse,
    JDCreateRequest,
    JDSummary,
    JDUploadResponse,
    LearningPlanResponse,
    ResumeDetail,
    ResumeSummary,
    ResumeUploadResponse,
    SkillGapResponse,
    SpeakRequest,
    TranscribeResponse,
    VoiceErrorResponse,
)
from app.graphs.candidate_graph import CandidateGraph
from app.middleware.auth import get_current_user
from app.services.jd_service import JDService
from app.services.resume_service import ResumeService
from app.services.interview_service import InterviewService
from app.services.voice_service import VoiceService

router = APIRouter()


# ═════════════════════════════════════════════════════════════════════════════
# RESUME
# ═════════════════════════════════════════════════════════════════════════════


@router.post(
    "/resume/upload",
    response_model=ResumeUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_resume(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
    resume_service: ResumeService = Depends(ResumeService),
):
    """Upload a PDF resume. Extracts text, parses via LLM, persists to Postgres."""
    result = await resume_service.upload_and_parse(file, current_user["id"])
    return ResumeUploadResponse(**result)


@router.get("/resumes", response_model=list[ResumeSummary])
async def list_my_resumes(
    current_user: dict = Depends(get_current_user),
    resume_service: ResumeService = Depends(ResumeService),
):
    """List all resumes for the current user (dashboard view)."""
    return await resume_service.list_user_resumes(current_user["id"])


@router.get("/resume/{resume_id}", response_model=ResumeDetail)
async def get_my_resume(
    resume_id: str,
    current_user: dict = Depends(get_current_user),
    resume_service: ResumeService = Depends(ResumeService),
):
    """Get one resume's parsed data + metadata. Enforces owner-only access."""
    return await resume_service.get_resume(resume_id, current_user["id"])


# ═════════════════════════════════════════════════════════════════════════════
# JOB DESCRIPTION (target JDs — for candidates comparing themselves against a role)
# ═════════════════════════════════════════════════════════════════════════════


@router.post(
    "/jd", response_model=JDUploadResponse, status_code=status.HTTP_201_CREATED
)
async def create_target_jd(
    body: JDCreateRequest,
    current_user: dict = Depends(get_current_user),
    jd_service: JDService = Depends(JDService),
):
    """Create a target JD from pasted text. Parsed via LLM and persisted."""
    return await jd_service.create_from_text(
        title=body.title,
        raw_text=body.raw_text,
        user_id=current_user["id"],
    )


@router.get("/jds", response_model=list[JDSummary])
async def list_my_jds(
    current_user: dict = Depends(get_current_user),
    jd_service: JDService = Depends(JDService),
):
    """List all JDs the current user has saved."""
    return await jd_service.list_user_jds(current_user["id"])


# ═════════════════════════════════════════════════════════════════════════════
# ATS SCORE / SKILL GAP / ROADMAP
# All three require an existing resume + JD in the DB.
# ═════════════════════════════════════════════════════════════════════════════


@router.get("/ats-score", response_model=ATSScoreResponse)
async def get_ats_score(
    resume_id: str,
    jd_id: str,
    current_user: dict = Depends(get_current_user),
    resume_service: ResumeService = Depends(ResumeService),
    jd_service: JDService = Depends(JDService),
):
    """Compute ATS score for a specific resume vs JD pair."""
    resume = await resume_service.get_resume(resume_id, current_user["id"])
    jd = await jd_service.get_jd(jd_id, current_user["id"])

    graph = CandidateGraph()
    result = await graph.run_ats_check(
        resume_data=resume["parsed_data"] or {},
        jd_data=jd["parsed_data"] or {},
        user_id=current_user["id"],
    )
    return ATSScoreResponse(resume_id=resume_id, jd_id=jd_id, **result)


@router.get("/skill-gap", response_model=SkillGapResponse)
async def get_skill_gap(
    resume_id: str,
    jd_id: str,
    current_user: dict = Depends(get_current_user),
    resume_service: ResumeService = Depends(ResumeService),
    jd_service: JDService = Depends(JDService),
):
    """Identify missing vs matching skills between resume and JD."""
    resume = await resume_service.get_resume(resume_id, current_user["id"])
    jd = await jd_service.get_jd(jd_id, current_user["id"])

    graph = CandidateGraph()
    result = await graph.run_skill_gap(
        resume_data=resume["parsed_data"] or {},
        jd_data=jd["parsed_data"] or {},
        user_id=current_user["id"],
    )
    return SkillGapResponse(resume_id=resume_id, jd_id=jd_id, **result)


@router.get("/learning-plan", response_model=LearningPlanResponse)
async def get_learning_plan(
    resume_id: str,
    jd_id: str,
    current_user: dict = Depends(get_current_user),
    resume_service: ResumeService = Depends(ResumeService),
    jd_service: JDService = Depends(JDService),
):
    """Generate a personalized learning roadmap based on skill gaps."""
    resume = await resume_service.get_resume(resume_id, current_user["id"])
    jd = await jd_service.get_jd(jd_id, current_user["id"])

    graph = CandidateGraph()
    result = await graph.run_learning_plan(
        resume_data=resume["parsed_data"] or {},
        jd_data=jd["parsed_data"] or {},
        user_id=current_user["id"],
    )
    return LearningPlanResponse(resume_id=resume_id, jd_id=jd_id, **result)


# ═════════════════════════════════════════════════════════════════════════════
# INTERVIEW (Phase 4)
# ═════════════════════════════════════════════════════════════════════════════


@router.post("/interview/start", response_model=InterviewStartResponse)
async def start_interview(
    body: InterviewStartRequest,
    current_user: dict = Depends(get_current_user),
    interview_service: InterviewService = Depends(InterviewService),
):
    return await interview_service.start_session(
        body.company, body.role, current_user["id"]
    )


@router.get("/interview/{session_id}", response_model=InterviewSessionResponse)
async def get_interview_session(
    session_id: str,
    current_user: dict = Depends(get_current_user),
    interview_service: InterviewService = Depends(InterviewService),
):
    return await interview_service.get_session(session_id, current_user["id"])


@router.post("/interview/{session_id}/respond", response_model=InterviewRespondResponse)
async def respond_to_interview(
    session_id: str,
    body: InterviewRespondRequest,
    current_user: dict = Depends(get_current_user),
    interview_service: InterviewService = Depends(InterviewService),
):
    return await interview_service.process_response(
        session_id, body.response, current_user["id"]
    )


@router.get(
    "/interview/{session_id}/feedback", response_model=InterviewFeedbackResponse
)
async def get_interview_feedback(
    session_id: str,
    current_user: dict = Depends(get_current_user),
    interview_service: InterviewService = Depends(InterviewService),
):
    return await interview_service.generate_feedback(session_id, current_user["id"])


# ═════════════════════════════════════════════════════════════════════════════
# VOICE (Phase 8)
# Listening: the browser records an answer, sends it here, and gets text back to edit
# and then send through /respond. The audio is read, sent to Groq and dropped: it is
# never written to disk or the database.
# Speaking: the browser asks for one of the interviewer's questions as audio. The
# question is read from the caller's own interview, never from the request.
# ═════════════════════════════════════════════════════════════════════════════

_AUDIO_TYPES = (
    "audio/webm",
    "audio/ogg",
    "audio/mp4",
    "audio/mpeg",
    "audio/wav",
    "audio/flac",
)

_TRANSCRIBE_ERRORS = {
    400: {"model": VoiceErrorResponse, "description": "No audio, or unreadable audio"},
    404: {"model": VoiceErrorResponse, "description": "Not your interview"},
    409: {"model": VoiceErrorResponse, "description": "Interview already complete"},
    413: {"model": VoiceErrorResponse, "description": "Recording too large or long"},
    415: {"model": VoiceErrorResponse, "description": "Recording format not supported"},
    422: {"model": VoiceErrorResponse, "description": "No speech was heard"},
    429: {"model": VoiceErrorResponse, "description": "Voice limit reached, or busy"},
    503: {"model": VoiceErrorResponse, "description": "Voice off or unavailable"},
}


def _voice_error(status_code: int, code: str, message: str) -> HTTPException:
    """Same error body as VoiceService: detail is {"code", "message"}."""
    return HTTPException(status_code, detail={"code": code, "message": message})


def _too_large() -> HTTPException:
    return _voice_error(
        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        "audio_rejected",
        "That recording is too long. Please keep your answer shorter.",
    )


async def _read_audio(request: Request, limit: int) -> bytes:
    """Read the request body, refusing to hold more than `limit` bytes.

    The audio is the raw request body (not a multipart upload), so nothing is spooled
    to a temporary file. An honest Content-Length is refused before reading anything;
    a missing or false one is caught while the body streams in.
    """
    try:
        announced = int(request.headers.get("content-length", ""))
    except ValueError:
        announced = None
    if announced is not None and announced > limit:
        raise _too_large()

    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise _too_large()
        chunks.append(chunk)
    return b"".join(chunks)


@router.post(
    "/interview/{session_id}/transcribe",
    response_model=TranscribeResponse,
    responses=_TRANSCRIBE_ERRORS,
    openapi_extra={
        "requestBody": {
            "required": True,
            "description": "The recording itself, as the raw request body. Send its "
            "type (for example audio/webm) as Content-Type.",
            "content": {
                kind: {"schema": {"type": "string", "format": "binary"}}
                for kind in _AUDIO_TYPES
            },
        }
    },
)
async def transcribe_interview_answer(
    session_id: str,
    request: Request,
    x_audio_duration: str | None = Header(
        default=None,
        description="Length of the recording in seconds, as the browser measured it. "
        "Optional; used to reserve the right amount of the voice limit.",
    ),
    current_user: dict = Depends(get_current_user),
    interview_service: InterviewService = Depends(InterviewService),
    voice_service: VoiceService = Depends(VoiceService),
):
    """Turn one spoken answer into text. The text is not saved or sent to the
    interviewer: the candidate edits it and sends it through /respond."""
    # Ownership and "still open" come first, so nobody else's session, and no
    # finished one, makes us read or send audio.
    try:
        session = await interview_service.get_session(session_id, current_user["id"])
    except HTTPException as exc:
        if exc.status_code == status.HTTP_404_NOT_FOUND:
            raise _voice_error(
                status.HTTP_404_NOT_FOUND,
                "not_found",
                "Interview session not found.",
            ) from exc
        raise
    if session["session_complete"]:
        raise _voice_error(
            status.HTTP_409_CONFLICT,
            "interview_complete",
            "This interview is already complete — view your feedback.",
        )

    audio = await _read_audio(request, voice_service.speech.max_audio_bytes)
    if not audio:
        raise _voice_error(
            status.HTTP_400_BAD_REQUEST,
            "audio_rejected",
            "No audio was received. Please try again.",
        )

    try:
        declared = float(x_audio_duration) if x_audio_duration else None
    except ValueError:
        declared = None  # a bad number is ignored, not an error

    result = await voice_service.transcribe_answer(
        current_user["id"],
        audio,
        request.headers.get("content-type", ""),
        declared,
    )
    return TranscribeResponse(**result)


_SPEAK_ERRORS = {
    400: {"model": VoiceErrorResponse, "description": "Not an interviewer question"},
    404: {
        "model": VoiceErrorResponse,
        "description": "Not your interview, or no such question",
    },
    409: {"model": VoiceErrorResponse, "description": "Interview already complete"},
    413: {
        "model": VoiceErrorResponse,
        "description": "Question too long to read aloud",
    },
    429: {"model": VoiceErrorResponse, "description": "Voice limit reached, or busy"},
    502: {
        "model": VoiceErrorResponse,
        "description": "The spoken clip came out silent",
    },
    503: {
        "model": VoiceErrorResponse,
        "description": "Spoken questions off or unavailable",
    },
}

_SPEAK_OK = {
    "description": "The question as a WAV file",
    "content": {"audio/wav": {"schema": {"type": "string", "format": "binary"}}},
    "headers": {
        "X-Voice-Cache": {
            "description": "hit: no new Groq call was made for this request; "
            "miss: the clip was just made",
            "schema": {"type": "string", "enum": ["hit", "miss"]},
        }
    },
}


def _question_to_read(messages: list[dict], index: int | None) -> str:
    """The text of the interviewer question to speak: the one at `index` in the
    session's messages, or the latest interviewer question when `index` is None."""
    if index is None:
        for message in reversed(messages):
            if message.get("role") == "interviewer":
                return message.get("content") or ""
    elif index < len(messages):
        message = messages[index]
        if message.get("role") != "interviewer":
            raise _voice_error(
                status.HTTP_400_BAD_REQUEST,
                "not_a_question",
                "Only the interviewer's questions can be read aloud.",
            )
        return message.get("content") or ""
    raise _voice_error(
        status.HTTP_404_NOT_FOUND,
        "question_not_found",
        "There is no such question in this interview.",
    )


@router.post(
    "/interview/{session_id}/speak",
    response_class=Response,
    responses={200: _SPEAK_OK, **_SPEAK_ERRORS},
)
async def speak_interview_question(
    session_id: str,
    body: SpeakRequest | None = None,
    current_user: dict = Depends(get_current_user),
    interview_service: InterviewService = Depends(InterviewService),
    voice_service: VoiceService = Depends(VoiceService),
):
    """Read one interviewer question aloud (Orpheus). Send no body to hear the latest
    question, or {"message_index": n} for the message at position n of the session.

    Only available when the server uses Orpheus (VOICE_TTS_PROVIDER=orpheus); the
    app otherwise reads questions with the browser's own voice and never calls this.
    """
    try:
        session = await interview_service.get_session(session_id, current_user["id"])
    except HTTPException as exc:
        if exc.status_code == status.HTTP_404_NOT_FOUND:
            raise _voice_error(
                status.HTTP_404_NOT_FOUND,
                "not_found",
                "Interview session not found.",
            ) from exc
        raise
    if session["session_complete"]:
        raise _voice_error(
            status.HTTP_409_CONFLICT,
            "interview_complete",
            "This interview is already complete — view your feedback.",
        )

    index = body.message_index if body else None
    text = _question_to_read(session["messages"], index)

    spoken = await voice_service.speak(current_user["id"], text)
    return Response(
        content=spoken["audio"],
        media_type="audio/wav",
        headers={
            "X-Voice-Cache": spoken["cache"],
            # The audio is a private question; the app keeps its own copy in memory.
            "Cache-Control": "private, no-store",
        },
    )
