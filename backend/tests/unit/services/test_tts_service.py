"""Unit tests for TtsService. Groq is replaced by an httpx mock transport that returns
audio made in the tests, so no test uses the network, an API key or quota."""

import inspect
import json
import logging
import random

import httpx
import pytest

from app.services import tts_service as ts
from app.services.speech_service import (
    SpeechError,
    SpeechNotConfigured,
    SpeechRateLimited,
    SpeechUnavailable,
)
from app.services.tts_service import (
    MAX_CHUNK_CHARS,
    MAX_TEXT_CHARS,
    SilentAudio,
    Speech,
    TextRejected,
    TtsService,
    clean_text,
    close_tts_client,
    split_text,
    used_by,
)
from app.services.wav_audio import PAUSE_SECONDS, WavClip
from tests.unit.services.test_wav_audio import orpheus_wav, silence, tone

KEY = "gsk_test_secret_key_123"
QUESTION = "Tell me about a time you had to debug a production issue under pressure."
GOOD = orpheus_wav(tone(1.0))
SILENT = orpheus_wav(silence(1.0))


def sentence(number: int, length: int = 70) -> str:
    """A sentence of exactly `length` characters, different for each number."""
    body = f"Sentence number {number} " + "word " * 40
    return body[: length - 1].rstrip() + "."


class FakeGroq:
    """Answers the speech endpoint, one scripted reply per request.

    `replies` holds what to answer, in order; when it runs out the answer is a good
    clip. An item is audio bytes, an `httpx.Response`, or an exception to raise.
    """

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.replies: list[object] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        reply = self.replies.pop(0) if self.replies else GOOD
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, httpx.Response):
            return reply
        return httpx.Response(200, content=reply, headers={"content-type": "audio/wav"})

    @property
    def sent(self) -> list[dict]:
        return [json.loads(r.content) for r in self.requests]

    def error(self, status, message="Something went wrong", **headers):
        self.replies.append(
            httpx.Response(
                status, json={"error": {"message": message}}, headers=headers
            )
        )


@pytest.fixture(autouse=True)
def key(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", KEY)
    monkeypatch.delenv("VOICE_TTS_MODEL", raising=False)
    monkeypatch.delenv("VOICE_TTS_VOICE", raising=False)


@pytest.fixture
def groq():
    return FakeGroq()


@pytest.fixture
async def service(groq):
    svc = TtsService()
    client = httpx.AsyncClient(transport=httpx.MockTransport(groq))
    svc.http = client
    ticks = iter(range(0, 1000))
    svc.clock = lambda: next(ticks) * 0.25  # every call to the clock moves 0.25 s
    yield svc
    await client.aclose()


async def failure(service, text=QUESTION) -> SpeechError:
    with pytest.raises(SpeechError) as caught:
        await service.synthesize(text)
    return caught.value


# ── Cleaning the text ─────────────────────────────────────────────────────────


class TestCleanText:
    def test_plain_text_is_unchanged(self):
        assert clean_text(QUESTION) == QUESTION

    def test_emphasis_and_code_marks_are_removed(self):
        assert clean_text("Explain **eventual consistency** and `CAP`.") == (
            "Explain eventual consistency and CAP."
        )

    def test_a_link_is_replaced_by_the_words_the_link(self):
        assert clean_text("Read https://example.com/a?b=c first.") == (
            "Read the link first."
        )

    def test_square_brackets_cannot_carry_a_spoken_direction(self):
        assert clean_text("Now [cheerful] describe a trade-off.") == (
            "Now cheerful describe a trade-off."
        )

    def test_angle_bracket_tags_are_removed(self):
        assert clean_text("Describe it <laugh> briefly.") == "Describe it briefly."

    def test_less_than_in_a_comparison_is_kept(self):
        assert clean_text("Is O(n) < O(n log n)?") == "Is O(n) < O(n log n)?"

    def test_headings_and_bullets_at_the_start_of_a_line_are_dropped(self):
        text = "## Question\n- first part\n• second part\n> quoted"

        assert clean_text(text) == "Question first part second part quoted"

    def test_a_hash_inside_a_word_is_kept(self):
        assert clean_text("Why choose C# for this?") == "Why choose C# for this?"

    def test_underscores_become_spaces(self):
        assert clean_text("the snake_case name") == "the snake case name"

    def test_new_lines_and_runs_of_spaces_become_single_spaces(self):
        assert clean_text("One.\n\n  Two.\tThree.") == "One. Two. Three."

    @pytest.mark.parametrize("value", ["", "   ", None])
    def test_nothing_in_gives_nothing_out(self, value):
        assert clean_text(value) == ""


# ── Splitting the text ────────────────────────────────────────────────────────


class TestSplitText:
    def test_a_short_question_is_one_chunk(self):
        assert split_text(QUESTION) == [QUESTION]

    @pytest.mark.parametrize("value", ["", "   ", "\n\t"])
    def test_nothing_to_split_gives_no_chunks(self, value):
        assert split_text(value) == []

    def test_exactly_the_limit_is_one_chunk(self):
        text = sentence(1, MAX_CHUNK_CHARS)

        assert len(text) == MAX_CHUNK_CHARS
        assert split_text(text) == [text]

    def test_one_character_over_the_limit_is_two_chunks(self):
        text = "a" * 100 + " " + "b" * 100  # 201 characters

        assert split_text(text) == ["a" * 100, "b" * 100]

    def test_sentences_are_packed_up_to_the_limit(self):
        sentences = [sentence(n) for n in range(1, 4)]  # three of 70 characters

        chunks = split_text(" ".join(sentences))

        assert chunks == [" ".join(sentences[:2]), sentences[2]]

    def test_chunks_break_at_sentence_ends_when_they_can(self):
        sentences = [sentence(n, 120) for n in range(1, 4)]

        chunks = split_text(" ".join(sentences))

        assert chunks == sentences

    def test_a_long_sentence_is_cut_at_a_comma(self):
        first = "word " * 25 + "end,"  # 129 characters
        second = "more " * 20 + "done."  # 105 characters

        assert split_text(f"{first} {second}") == [first, second]

    def test_a_long_sentence_with_no_commas_is_cut_at_a_space(self):
        text = " ".join(f"word{n:02d}" for n in range(60))  # 419 characters

        chunks = split_text(text)

        assert len(chunks) == 3
        assert all(len(c) <= MAX_CHUNK_CHARS for c in chunks)
        assert " ".join(chunks) == text  # cut at spaces only: no word is broken

    def test_a_word_longer_than_the_limit_is_cut_where_it_stands(self):
        text = "x" * 450

        assert split_text(text) == ["x" * 200, "x" * 200, "x" * 50]

    def test_the_limit_can_be_changed(self):
        assert split_text("One two three four five six", limit=12) == [
            "One two",
            "three four",
            "five six",
        ]

    def test_the_text_is_trimmed_and_its_spaces_collapsed(self):
        assert split_text("  Hello    there.  ") == ["Hello there."]

    def test_a_realistic_three_sentence_question_is_two_chunks(self):
        text = (
            "Walk me through how you would design a rate limiter for a public API. "
            "Which algorithm would you choose, and why? "
            "Then tell me how you would test it under heavy traffic, and how you "
            "would roll it out safely to production."
        )

        chunks = split_text(text)

        assert len(chunks) == 2
        assert chunks[0].endswith("and why?")

    def test_every_chunk_is_in_range_and_nothing_is_dropped(self):
        """For many random texts: chunks are never empty, never over the limit, and
        apart from spaces they add up to exactly the text."""
        generator = random.Random(11)
        pieces = [
            "a",
            "bb",
            "ccc",
            "dddd",
            "word",
            "x" * 30,
            "y" * 250,
            "Hi.",
            "a,",
            "b?",
        ]
        for _ in range(400):
            words = [generator.choice(pieces) for _ in range(generator.randint(1, 80))]
            text = " ".join(words)
            limit = generator.choice([10, 25, 80, 200])

            chunks = split_text(text, limit)

            assert chunks, text
            assert all(chunks), chunks
            assert all(len(c) <= limit for c in chunks), (limit, chunks)
            assert "".join("".join(chunks).split()) == "".join(text.split())


# ── Preparing a call ──────────────────────────────────────────────────────────


class TestPrepare:
    def test_returns_the_chunks_that_would_be_sent(self):
        assert TtsService().prepare(f"**{QUESTION}**") == [QUESTION]

    @pytest.mark.parametrize("text", ["", "   ", "...", "** **", "[ ]", "- ", None])
    def test_text_with_nothing_to_say_is_rejected(self, text):
        with pytest.raises(TextRejected) as caught:
            TtsService().prepare(text)

        assert caught.value.status == 400
        assert caught.value.code == "text_rejected"

    def test_text_over_the_limit_is_rejected_as_too_large(self):
        with pytest.raises(TextRejected) as caught:
            TtsService().prepare("a" * (MAX_TEXT_CHARS + 1))

        assert caught.value.status == 413

    def test_text_exactly_at_the_limit_is_accepted(self):
        chunks = TtsService().prepare("a" * MAX_TEXT_CHARS)

        assert sum(len(c) for c in chunks) == MAX_TEXT_CHARS

    def test_the_limit_is_measured_after_cleaning(self):
        text = "**" + "a" * MAX_TEXT_CHARS + "**"

        assert TtsService().prepare(text)

    def test_the_limit_can_be_changed(self):
        svc = TtsService()
        svc.max_chars = 20

        with pytest.raises(TextRejected):
            svc.prepare("a" * 21)

    def test_the_rejection_message_does_not_contain_the_text(self):
        text = "SECRET-QUESTION-TEXT " * 60

        with pytest.raises(TextRejected) as caught:
            TtsService().prepare(text)

        assert "SECRET" not in str(caught.value)
        assert "SECRET" not in caught.value.detail


# ── Settings ──────────────────────────────────────────────────────────────────


class TestSettings:
    def test_defaults(self):
        svc = TtsService()

        assert svc.model == "canopylabs/orpheus-v1-english"
        assert svc.voice == "daniel"
        assert svc.max_chars == MAX_TEXT_CHARS

    def test_model_and_voice_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("VOICE_TTS_MODEL", "canopylabs/orpheus-next")
        monkeypatch.setenv("VOICE_TTS_VOICE", "hannah")

        svc = TtsService()

        assert svc.model == "canopylabs/orpheus-next"
        assert svc.voice == "hannah"

    def test_blank_settings_keep_the_defaults(self, monkeypatch):
        monkeypatch.setenv("VOICE_TTS_MODEL", "  ")
        monkeypatch.setenv("VOICE_TTS_VOICE", "")

        svc = TtsService()

        assert svc.model == "canopylabs/orpheus-v1-english"
        assert svc.voice == "daniel"

    def test_constructor_takes_no_arguments(self):
        assert list(inspect.signature(TtsService.__init__).parameters) == ["self"]

    def test_the_chunk_limit_is_orpheus_limit(self):
        assert MAX_CHUNK_CHARS == 200


# ── A good clip ───────────────────────────────────────────────────────────────


class TestSynthesize:
    async def test_returns_one_clip_and_what_it_cost(self, service, groq):
        speech = await service.synthesize(QUESTION)

        assert isinstance(speech, Speech)
        assert speech.characters == len(QUESTION)
        assert speech.chunks == 1
        assert speech.clip.seconds == pytest.approx(1.0)
        assert speech.clip.has_sound()
        assert speech.latency_s > 0
        assert len(groq.requests) == 1

    async def test_sends_what_orpheus_expects(self, service, groq):
        await service.synthesize(QUESTION)

        request = groq.requests[0]
        assert str(request.url) == ts.TTS_URL
        assert request.headers["Authorization"] == f"Bearer {KEY}"
        assert groq.sent == [
            {
                "model": "canopylabs/orpheus-v1-english",
                "input": QUESTION,
                "voice": "daniel",
                "response_format": "wav",
            }
        ]

    async def test_uses_the_configured_model_and_voice(self, service, groq):
        service.model, service.voice = "other-model", "hannah"

        await service.synthesize(QUESTION)

        assert groq.sent[0]["model"] == "other-model"
        assert groq.sent[0]["voice"] == "hannah"

    async def test_sends_the_cleaned_text(self, service, groq):
        await service.synthesize(
            f"**Question:** see https://example.com [cheerful] {QUESTION}"
        )

        assert groq.sent[0]["input"] == f"Question: see the link cheerful {QUESTION}"

    async def test_the_clip_can_be_written_as_a_clean_wav_file(self, service):
        speech = await service.synthesize(QUESTION)

        data = speech.clip.to_bytes()

        assert data[:4] == b"RIFF"
        assert len(data) == 44 + len(speech.clip.pcm)

    async def test_the_timeout_is_passed_on(self, service, groq):
        service.timeout = 7

        await service.synthesize(QUESTION)

        assert groq.requests[0].extensions["timeout"]["read"] == 7

    async def test_a_long_question_is_sent_in_chunks_and_joined(self, service, groq):
        sentences = [sentence(n, 120) for n in range(1, 4)]

        speech = await service.synthesize(" ".join(sentences))

        assert [body["input"] for body in groq.sent] == sentences
        assert speech.chunks == 3
        assert speech.characters == sum(len(s) for s in sentences)
        assert speech.clip.seconds == pytest.approx(3.0 + 2 * PAUSE_SECONDS, abs=0.001)

    async def test_no_retry_after_an_error(self, service, groq):
        groq.error(500)

        await failure(service)

        assert len(groq.requests) == 1

    async def test_the_shared_client_is_used_when_none_is_set(
        self, service, groq, monkeypatch
    ):
        shared = httpx.AsyncClient(transport=httpx.MockTransport(groq))
        monkeypatch.setattr(ts, "_shared_client", lambda: shared)
        service.http = None

        await service.synthesize(QUESTION)
        await shared.aclose()

        assert len(groq.requests) == 1

    async def test_text_with_nothing_to_say_makes_no_call(self, service, groq):
        error = await failure(service, "...")

        assert isinstance(error, TextRejected)
        assert groq.requests == []
        assert used_by(error) == 0


# ── Errors ────────────────────────────────────────────────────────────────────


class TestErrors:
    async def test_no_key_is_not_configured_and_makes_no_call(
        self, service, groq, monkeypatch
    ):
        monkeypatch.delenv("GROQ_API_KEY")

        error = await failure(service)

        assert isinstance(error, SpeechNotConfigured)
        assert error.status == 503
        assert groq.requests == []
        assert used_by(error) == 0

    async def test_a_rate_limit_carries_groqs_wait(self, service, groq):
        groq.error(429, "Rate limit reached", **{"retry-after": "12"})

        error = await failure(service)

        assert isinstance(error, SpeechRateLimited)
        assert error.status == 429
        assert error.retry_after == 12
        assert used_by(error) == 0

    async def test_a_rate_limit_without_a_wait_has_none(self, service, groq):
        groq.error(429)

        error = await failure(service)

        assert isinstance(error, SpeechRateLimited)
        assert error.retry_after is None

    @pytest.mark.parametrize("status", [401, 403])
    async def test_a_rejected_key_is_not_configured(self, service, groq, status):
        groq.error(status)

        error = await failure(service)

        assert isinstance(error, SpeechNotConfigured)
        assert error.status == 503

    async def test_orpheus_terms_not_accepted_is_not_configured(self, service, groq):
        groq.error(400, "The model requires terms acceptance in the console")

        error = await failure(service)

        assert isinstance(error, SpeechNotConfigured)
        assert "terms" in error.detail

    @pytest.mark.parametrize(
        ("status", "expected"), [(400, 400), (422, 400), (413, 413)]
    )
    async def test_text_groq_refuses_is_text_rejected(
        self, service, groq, status, expected
    ):
        groq.error(status, "input too long")

        error = await failure(service)

        assert isinstance(error, TextRejected)
        assert error.status == expected
        assert used_by(error) == 0

    @pytest.mark.parametrize("status", [404, 500, 502, 503])
    async def test_other_http_errors_are_unavailable(self, service, groq, status):
        groq.error(status, "model_decommissioned")

        error = await failure(service)

        assert isinstance(error, SpeechUnavailable)
        assert error.status == 503
        assert f"HTTP {status}" in error.detail

    async def test_a_timeout_is_unavailable(self, service, groq):
        groq.replies.append(httpx.ReadTimeout("slow"))

        error = await failure(service)

        assert isinstance(error, SpeechUnavailable)
        assert error.detail == "timed out"
        assert used_by(error) == 0

    async def test_a_network_failure_is_unavailable(self, service, groq):
        groq.replies.append(httpx.ConnectError("no route"))

        error = await failure(service)

        assert isinstance(error, SpeechUnavailable)
        assert error.detail == "ConnectError"

    async def test_audio_that_cannot_be_read_is_unavailable_and_was_charged(
        self, service, groq
    ):
        groq.replies.append(b"this is not audio at all")

        error = await failure(service)

        assert isinstance(error, SpeechUnavailable)
        assert "unreadable audio" in error.detail
        assert used_by(error) == len(QUESTION)

    async def test_a_silent_clip_is_refused_and_was_charged(self, service, groq):
        groq.replies.append(SILENT)

        error = await failure(service)

        assert isinstance(error, SilentAudio)
        assert error.code == "silent_audio"
        assert error.status == 502
        assert used_by(error) == len(QUESTION)

    async def test_a_nearly_silent_clip_with_clicks_is_refused(self, service, groq):
        from array import array

        samples = array("h", [0] * 24000)
        for position in range(0, 24000, 100):
            samples[position] = 32767
        groq.replies.append(orpheus_wav(samples.tobytes()))

        error = await failure(service)

        assert isinstance(error, SilentAudio)

    async def test_a_clip_with_no_samples_is_refused(self, service, groq):
        groq.replies.append(orpheus_wav(b""))

        error = await failure(service)

        assert isinstance(error, SilentAudio)

    async def test_an_error_in_a_later_chunk_reports_what_was_already_charged(
        self, service, groq
    ):
        sentences = [sentence(n, 120) for n in range(1, 4)]
        groq.replies = [GOOD, GOOD]
        groq.error(429, "Rate limit reached", **{"retry-after": "30"})

        error = await failure(service, " ".join(sentences))

        assert isinstance(error, SpeechRateLimited)
        assert used_by(error) == len(sentences[0]) + len(sentences[1])
        assert len(groq.requests) == 3

    async def test_a_silent_first_chunk_stops_before_the_next_one(self, service, groq):
        sentences = [sentence(n, 120) for n in range(1, 4)]
        groq.replies = [SILENT]

        error = await failure(service, " ".join(sentences))

        assert isinstance(error, SilentAudio)
        assert used_by(error) == len(sentences[0])
        assert len(groq.requests) == 1

    async def test_a_silent_last_chunk_charges_all_of_them(self, service, groq):
        sentences = [sentence(n, 120) for n in range(1, 4)]
        groq.replies = [GOOD, GOOD, SILENT]

        error = await failure(service, " ".join(sentences))

        assert isinstance(error, SilentAudio)
        assert used_by(error) == sum(len(s) for s in sentences)
        assert "chunk 3 of 3" in error.detail

    async def test_chunks_in_different_formats_cannot_be_joined(self, service, groq):
        sentences = [sentence(n, 120) for n in range(1, 3)]
        other_rate = WavClip(1, 16000, tone(1.0, rate=16000)).to_bytes()
        groq.replies = [GOOD, other_rate]

        error = await failure(service, " ".join(sentences))

        assert isinstance(error, SpeechUnavailable)
        assert used_by(error) == sum(len(s) for s in sentences)

    async def test_every_error_is_a_speech_error_with_a_message_that_is_safe_to_show(
        self, service, groq
    ):
        for reply in (
            httpx.Response(429, json={"error": {"message": f"key {KEY}"}}),
            httpx.Response(401, json={"error": {"message": f"bad key {KEY}"}}),
            httpx.Response(500, json={"error": {"message": f"oops {KEY}"}}),
            httpx.Response(400, json={"error": {"message": f"bad {KEY} {QUESTION}"}}),
            SILENT,
        ):
            groq.replies.append(reply)
            error = await failure(service)

            assert isinstance(error, SpeechError)
            assert KEY not in str(error) and KEY not in error.detail
            assert "read the question on the screen" in str(error) or (
                "read it on the screen" in str(error)
            )

    def test_the_error_types(self):
        assert issubclass(TextRejected, SpeechError)
        assert issubclass(SilentAudio, SpeechError)
        assert (TextRejected.code, TextRejected.status) == ("text_rejected", 400)
        assert (SilentAudio.code, SilentAudio.status) == ("silent_audio", 502)


class TestUsedBy:
    def test_reads_the_number_from_an_error(self):
        error = SpeechUnavailable("x")
        error.characters_used = 42

        assert used_by(error) == 42

    @pytest.mark.parametrize("value", [None, 0, ""])
    def test_a_missing_or_empty_number_is_zero(self, value):
        error = SpeechUnavailable("x")
        error.characters_used = value

        assert used_by(error) == 0

    def test_an_error_without_it_is_zero(self):
        assert used_by(RuntimeError("boom")) == 0


# ── Privacy ───────────────────────────────────────────────────────────────────


class TestPrivacy:
    async def test_the_text_and_the_key_are_never_logged(self, service, groq, caplog):
        secret = "ZEBRA-QUESTION-ABOUT-CONFIDENTIAL-PROJECT"
        groq.replies = [GOOD, SILENT]
        text = f"{secret} {sentence(1, 150)} {sentence(2, 150)}"

        with caplog.at_level(logging.DEBUG):
            await failure(service, text)

        assert secret not in caplog.text
        assert KEY not in caplog.text
        assert "Text to speech" in caplog.text  # counts and status are logged

    async def test_a_request_logs_only_counts_and_timing(self, service, caplog):
        with caplog.at_level(logging.INFO):
            await service.synthesize(QUESTION)

        assert QUESTION not in caplog.text
        assert f"{len(QUESTION)} chars" in caplog.text
        assert "status 200" in caplog.text


# ── The shared client ─────────────────────────────────────────────────────────


class TestSharedClient:
    async def test_it_is_created_once_and_closed_on_shutdown(self):
        await close_tts_client()
        first = ts._shared_client()

        assert ts._shared_client() is first

        await close_tts_client()

        assert first.is_closed
        assert ts._client is None
        assert ts._shared_client() is not first
        await close_tts_client()

    async def test_closing_when_nothing_was_opened_is_fine(self):
        await close_tts_client()
        await close_tts_client()

        assert ts._client is None
