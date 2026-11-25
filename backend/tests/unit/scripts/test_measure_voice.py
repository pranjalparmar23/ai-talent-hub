"""Unit tests for scripts/measure_voice.py.

Groq is replaced by `FakeGroq`, an httpx mock transport, so no test uses real quota
or the network. Time is a fake clock that only moves when the script sleeps or a
fake call takes time.
"""

import json
import struct
import wave
from datetime import datetime, timezone
from io import BytesIO

import httpx
import pytest

from scripts import measure_voice as mv

KEY = "gsk_test_secret_key_123"
FIXED_NOW = datetime(2026, 11, 25, 12, 0, 0, tzinfo=timezone.utc)


def make_wav(
    seconds: float = 2.0,
    rate: int = 8000,
    *,
    lead: float = 0.0,
    level: int = 8000,
    streaming_header: bool = False,
) -> bytes:
    """A mono 16-bit WAV: `lead` seconds of silence, then a loud tone."""
    silent = int(lead * rate)
    loud = int(seconds * rate) - silent
    frames = b"\x00\x00" * silent + struct.pack("<h", level) * loud
    buffer = BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(frames)
    data = bytearray(buffer.getvalue())
    if streaming_header:
        # Orpheus-style placeholder lengths: the header claims about 2 billion frames
        data[4:8] = (0x7FFFFFFF).to_bytes(4, "little")
        data[40:44] = (0x7FFFFFFF).to_bytes(4, "little")
    return bytes(data)


class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class FakeGroq:
    """Answers the two Groq endpoints the script calls."""

    def __init__(self, clock: FakeClock):
        self.clock = clock
        self.requests: list[httpx.Request] = []
        self.tts_inputs: list[str] = []
        self.tts_count = 0
        self.stt_count = 0
        self.tokens_per_call = 60
        self.limit_tokens = 1200
        self.wav_factory = lambda: make_wav(2.0)
        self.bucket = 1200.0
        self.bucket_time = clock.now
        self.fail_tts_on: int | None = None  # 1-based call number
        self.fail_status = 429
        self.fail_message = "Rate limit reached."
        self.raise_on_tts: Exception | None = None
        self.transcript_for = lambda text: text
        self.include_words = True
        self.words_on_every_call = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path.endswith("/audio/speech"):
            return self._speech(request)
        return self._transcription(request)

    def _speech(self, request):
        self.tts_count += 1
        body = json.loads(request.content)
        self.tts_inputs.append(body["input"])
        self.clock.now += 1.5
        if self.raise_on_tts:
            raise self.raise_on_tts
        if self.fail_tts_on == self.tts_count:
            return httpx.Response(
                self.fail_status,
                json={"error": {"message": self.fail_message}},
                headers={"retry-after": "42"},
            )
        # a bucket that refills all the time, like Groq's per-minute token figure
        elapsed = self.clock.now - self.bucket_time
        refilled = self.bucket + self.limit_tokens / 60 * elapsed
        self.bucket = min(self.limit_tokens, refilled) - self.tokens_per_call
        self.bucket_time = self.clock.now
        remaining = round(self.bucket)
        return httpx.Response(
            200,
            content=self.wav_factory(),
            headers={
                "x-ratelimit-limit-tokens": str(self.limit_tokens),
                "x-ratelimit-remaining-tokens": str(remaining),
                "x-ratelimit-remaining-requests": str(100 - self.tts_count),
                "content-type": "audio/wav",
            },
        )

    def _transcription(self, request):
        self.stt_count += 1
        self.clock.now += 0.4
        text = self.transcript_for(self.tts_inputs[-1])
        wants_words = b"timestamp_granularities" in request.content
        body: dict = {"text": text}
        if self.include_words and wants_words:
            body["words"] = [{"word": w, "start": 0, "end": 1} for w in text.split()]
        return httpx.Response(200, json=body)


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def groq(clock):
    return FakeGroq(clock)


def run_script(groq, clock, tmp_path, argv=None, key=KEY):
    client = httpx.Client(transport=httpx.MockTransport(groq))
    env = {"GROQ_API_KEY": key} if key is not None else {}
    code = mv.run(
        argv or [],
        env=env,
        client=client,
        sleep=clock.sleep,
        clock=clock,
        now=lambda: FIXED_NOW,
        results_dir=tmp_path,
    )
    return code


def saved(tmp_path) -> dict:
    files = list(tmp_path.glob("voice_latency_*.json"))
    assert len(files) == 1
    return json.loads(files[0].read_text(encoding="utf-8"))


# ── Pure helpers ──────────────────────────────────────────────────────────────


class TestWordErrorRate:
    def test_identical_text_is_zero(self):
        assert mv.word_error_rate("tell me about it", "tell me about it") == 0.0

    def test_case_and_punctuation_are_ignored(self):
        assert mv.word_error_rate("Tell me, about it.", "tell me about IT") == 0.0

    def test_one_wrong_word_in_four(self):
        assert mv.word_error_rate("a b c d", "a b x d") == 0.25

    def test_a_missing_word_counts(self):
        assert mv.word_error_rate("a b c d", "a b d") == 0.25

    def test_an_extra_word_counts(self):
        assert mv.word_error_rate("a b c d", "a b c x d") == 0.25

    def test_empty_transcript_is_total_error(self):
        assert mv.word_error_rate("a b", "") == 1.0

    def test_no_reference_words(self):
        assert mv.word_error_rate("", "") == 0.0
        assert mv.word_error_rate("", "word") == 1.0


class TestWavSeconds:
    def test_reads_the_length(self):
        assert mv.wav_seconds(make_wav(2.5)) == pytest.approx(2.5)

    def test_not_a_wav_gives_none(self):
        assert mv.wav_seconds(b"not audio") is None
        assert mv.wav_seconds(b"") is None

    def test_streaming_header_uses_the_bytes_received(self):
        # Bug found in the first real run: the header said 89,478 seconds.
        audio = make_wav(2.5, streaming_header=True)

        assert mv.wav_seconds(audio) == pytest.approx(2.5)

    def test_a_cut_off_file_is_measured_by_what_is_there(self):
        audio = make_wav(2.0)

        assert mv.wav_seconds(audio[: len(audio) // 2]) == pytest.approx(1.0, abs=0.01)


class TestAudioIsDefective:
    def test_speech_like_clip_is_fine(self):
        assert not mv.audio_is_defective(mv.wav_info(make_wav(2.0)))

    def test_silent_clip_is_defective(self):
        assert mv.audio_is_defective(mv.wav_info(make_wav(2.0, level=0)))

    def test_mostly_silent_clip_with_a_short_burst_is_defective(self):
        # like the real clip 4: seven seconds of silence, then a loud burst
        assert mv.audio_is_defective(mv.wav_info(make_wav(2.0, lead=1.8)))

    def test_unknown_audio_is_not_called_defective(self):
        assert not mv.audio_is_defective(None)
        assert not mv.audio_is_defective({"seconds": 1.0})


class TestWavInfo:
    def test_loudness_and_silence(self):
        info = mv.wav_info(make_wav(2.0, lead=0.5, level=16384))

        assert info["seconds"] == pytest.approx(2.0)
        assert info["peak"] == pytest.approx(0.5, abs=0.001)
        assert info["leading_silence_s"] == pytest.approx(0.5, abs=0.01)
        assert info["trailing_silence_s"] == pytest.approx(0.0, abs=0.01)

    def test_a_silent_file_is_all_silence(self):
        info = mv.wav_info(make_wav(1.0, level=0))

        assert info["peak"] == 0.0
        assert info["leading_silence_s"] == pytest.approx(1.0)

    def test_not_a_wav_gives_none(self):
        assert mv.wav_info(b"nope") is None


class TestPickHeaders:
    def test_keeps_only_rate_limit_headers_in_lower_case(self):
        headers = {
            "X-RateLimit-Remaining-Tokens": "1140",
            "Retry-After": "9",
            "Content-Type": "audio/wav",
            "Set-Cookie": "secret",
        }
        assert mv.pick_headers(headers) == {
            "x-ratelimit-remaining-tokens": "1140",
            "retry-after": "9",
        }


class TestEstimateTokens:
    LIMIT = "1200"

    def headers(self, remaining):
        return {
            "x-ratelimit-limit-tokens": self.LIMIT,
            "x-ratelimit-remaining-tokens": str(remaining),
        }

    def test_first_call_started_with_a_full_bucket(self):
        assert mv.estimate_tokens_used(None, self.headers(1064), None) == 136

    def test_bucket_that_refilled_between_calls_counts_from_full(self):
        # Real run: 1,064 left, 8 s later 1,057 left. The bucket had refilled
        # (20 tokens a second), so the second call used 143, not 7.
        assert (
            mv.estimate_tokens_used(self.headers(1064), self.headers(1057), 8.0) == 143
        )

    def test_bucket_that_is_still_refilling(self):
        # 900 left, 2 s later the bucket holds 900 + 40 = 940; 880 left means 60 used.
        assert mv.estimate_tokens_used(self.headers(900), self.headers(880), 2.0) == 60

    def test_never_negative(self):
        assert mv.estimate_tokens_used(self.headers(100), self.headers(1200), 1.0) == 0

    def test_missing_headers_give_none(self):
        assert mv.estimate_tokens_used(None, {}, None) is None
        assert (
            mv.estimate_tokens_used(None, {"x-ratelimit-remaining-tokens": "5"}, None)
            is None
        )


class TestRedact:
    def test_removes_the_key(self):
        assert mv.redact(f"bad key {KEY} given", KEY) == "bad key [redacted] given"

    def test_empty_secret_changes_nothing(self):
        assert mv.redact("text", "") == "text"


class TestSentences:
    def test_each_fits_one_orpheus_request(self):
        assert all(0 < len(s) <= mv.MAX_TTS_CHARS for s in mv.SENTENCES)

    def test_are_different(self):
        assert len(set(mv.SENTENCES)) == len(mv.SENTENCES)


# ── Dry run and start-up ──────────────────────────────────────────────────────


class TestDryRun:
    def test_makes_no_calls_and_writes_nothing(self, groq, clock, tmp_path, capsys):
        code = run_script(groq, clock, tmp_path, ["--dry-run"])

        assert code == 0
        assert groq.requests == []
        assert list(tmp_path.iterdir()) == []
        out = capsys.readouterr().out
        assert "dry run" in out
        assert "8" in out  # four speech + four transcription calls

    def test_does_not_need_a_key(self, groq, clock, tmp_path, capsys):
        assert run_script(groq, clock, tmp_path, ["--dry-run"], key=None) == 0
        assert "NOT found" in capsys.readouterr().out

    def test_never_prints_the_key(self, groq, clock, tmp_path, capsys):
        run_script(groq, clock, tmp_path, ["--dry-run"])
        assert KEY not in capsys.readouterr().out


class TestStart:
    def test_missing_key_stops_before_any_call(self, groq, clock, tmp_path, capsys):
        code = run_script(groq, clock, tmp_path, [], key=None)

        assert code == 2
        assert groq.requests == []
        assert list(tmp_path.iterdir()) == []
        assert "GROQ_API_KEY" in capsys.readouterr().err

    def test_blank_key_counts_as_missing(self, groq, clock, tmp_path):
        assert run_script(groq, clock, tmp_path, [], key="   ") == 2

    @pytest.mark.parametrize("value", ["0", "5", "-1", "x"])
    def test_chunks_outside_the_range_are_refused(self, groq, clock, tmp_path, value):
        with pytest.raises(SystemExit) as exit_info:
            run_script(groq, clock, tmp_path, ["--chunks", value])
        assert exit_info.value.code == 2
        assert groq.requests == []


# ── A full run ────────────────────────────────────────────────────────────────


class TestFullRun:
    def test_makes_four_speech_and_four_transcription_calls(
        self, groq, clock, tmp_path
    ):
        code = run_script(groq, clock, tmp_path)

        assert code == 0
        assert groq.tts_count == 4
        assert groq.stt_count == 4
        assert groq.tts_inputs == list(mv.SENTENCES)

    def test_sends_the_key_and_the_settings(self, groq, clock, tmp_path):
        run_script(groq, clock, tmp_path, ["--chunks", "1", "--voice", "troy"])

        speech = json.loads(groq.requests[0].content)
        assert groq.requests[0].headers["authorization"] == f"Bearer {KEY}"
        assert speech["model"] == mv.DEFAULT_TTS_MODEL
        assert speech["voice"] == "troy"
        assert speech["response_format"] == "wav"
        assert b"whisper-large-v3-turbo" in groq.requests[1].content

    def test_spaces_the_speech_calls(self, groq, clock, tmp_path):
        run_script(groq, clock, tmp_path)

        # three gaps between four speech calls, none shorter than the safe minimum
        assert len(clock.sleeps) == 3
        assert all(wait > 4.0 for wait in clock.sleeps)

    def test_asks_for_word_timestamps_only_on_the_last_sentence(
        self, groq, clock, tmp_path
    ):
        run_script(groq, clock, tmp_path)

        stt = [r for r in groq.requests if r.url.path.endswith("/transcriptions")]
        flags = [b"timestamp_granularities" in r.content for r in stt]
        assert flags == [False, False, False, True]
        assert b"verbose_json" in stt[-1].content

    def test_saves_one_result_file(self, groq, clock, tmp_path):
        run_script(groq, clock, tmp_path)

        document = saved(tmp_path)
        assert tmp_path.joinpath("voice_latency_20261125T120000Z.json").exists()
        assert document["kind"] == "voice_latency"
        assert document["created"] == "2026-11-25T12:00:00Z"
        assert len(document["tts"]) == 4
        assert len(document["stt"]) == 4
        assert document["stopped"] is None
        assert document["sentence_chars"] == [len(s) for s in mv.SENTENCES]

    def test_summary_numbers(self, groq, clock, tmp_path):
        run_script(groq, clock, tmp_path)

        summary = saved(tmp_path)["summary"]
        assert summary["tts_latency_s"] == {"median": 1.5, "max": 1.5}
        assert summary["stt_latency_s"] == {"median": 0.4, "max": 0.4}
        assert summary["stt_real_time_factor_median"] == pytest.approx(0.2)
        assert summary["wer"] == {"mean": 0.0, "max": 0.0}
        assert summary["tokens_used_estimate_median"] == 60
        assert summary["word_timestamps"]["present"] is True
        assert summary["word_timestamps"]["count"] > 0

    def test_counts_the_audio(self, groq, clock, tmp_path):
        run_script(groq, clock, tmp_path)

        first = saved(tmp_path)["tts"][0]
        assert first["audio_s"] == pytest.approx(2.0)
        assert first["bytes"] > 0
        assert first["chars"] == len(mv.SENTENCES[0])

    def test_a_wrong_transcript_gives_a_word_error_rate(self, groq, clock, tmp_path):
        groq.transcript_for = lambda text: " ".join(text.split()[:-3])

        run_script(groq, clock, tmp_path, ["--chunks", "1"])

        record = saved(tmp_path)["stt"][0]
        assert 0 < record["wer"] < 1
        assert record["transcript"]

    def test_missing_word_timestamps_are_reported(self, groq, clock, tmp_path):
        groq.include_words = False

        run_script(groq, clock, tmp_path, ["--chunks", "1"])

        assert saved(tmp_path)["summary"]["word_timestamps"]["present"] is False

    def test_the_result_file_has_no_key_and_no_audio(self, groq, clock, tmp_path):
        run_script(groq, clock, tmp_path)

        names = [p.name for p in tmp_path.iterdir()]
        assert len(names) == 1 and names[0].endswith(".json")
        text = (tmp_path / names[0]).read_text(encoding="utf-8")
        assert KEY not in text
        assert "RIFF" not in text

    def test_only_rate_limit_headers_are_saved(self, groq, clock, tmp_path):
        run_script(groq, clock, tmp_path, ["--chunks", "1"])

        headers = saved(tmp_path)["tts"][0]["headers"]
        assert set(headers) <= {
            "x-ratelimit-limit-tokens",
            "x-ratelimit-remaining-tokens",
            "x-ratelimit-remaining-requests",
        }
        assert "content-type" not in headers

    def test_one_sentence_makes_no_wait(self, groq, clock, tmp_path):
        run_script(groq, clock, tmp_path, ["--chunks", "1"])

        assert clock.sleeps == []
        assert groq.tts_count == 1 and groq.stt_count == 1


# ── Stopping early ────────────────────────────────────────────────────────────


class TestStopsEarly:
    def test_a_429_stops_the_run_and_keeps_what_was_measured(
        self, groq, clock, tmp_path
    ):
        groq.fail_tts_on = 2

        code = run_script(groq, clock, tmp_path)

        assert code == 1
        assert groq.tts_count == 2  # no retry, no third call
        assert groq.stt_count == 1
        document = saved(tmp_path)
        assert len(document["tts"]) == 1 and len(document["stt"]) == 1
        stopped = document["stopped"]
        assert stopped["stage"] == "tts"
        assert stopped["status"] == 429
        assert stopped["headers"]["retry-after"] == "42"
        assert "retry-after" in stopped["hint"]

    def test_terms_error_says_what_to_do(self, groq, clock, tmp_path):
        groq.fail_tts_on = 1
        groq.fail_status = 403
        groq.fail_message = "You must accept the terms for this model first."

        code = run_script(groq, clock, tmp_path)

        assert code == 1
        stopped = saved(tmp_path)["stopped"]
        assert stopped["status"] == 403
        assert "terms" in stopped["hint"]
        assert groq.stt_count == 0

    def test_rejected_key(self, groq, clock, tmp_path):
        groq.fail_tts_on = 1
        groq.fail_status = 401
        groq.fail_message = "Invalid API Key"

        assert run_script(groq, clock, tmp_path) == 1
        assert "GROQ_API_KEY" in saved(tmp_path)["stopped"]["hint"]

    def test_timeout(self, groq, clock, tmp_path):
        groq.raise_on_tts = httpx.ReadTimeout("slow")

        code = run_script(groq, clock, tmp_path)

        assert code == 1
        stopped = saved(tmp_path)["stopped"]
        assert stopped["status"] is None
        assert "timed out" in stopped["message"]
        assert groq.tts_count == 1

    def test_network_error(self, groq, clock, tmp_path):
        groq.raise_on_tts = httpx.ConnectError("down")

        assert run_script(groq, clock, tmp_path) == 1
        assert "Network error" in saved(tmp_path)["stopped"]["message"]

    def test_an_error_that_echoes_the_key_is_redacted(self, groq, clock, tmp_path):
        groq.fail_tts_on = 1
        groq.fail_status = 401
        groq.fail_message = f"Invalid API Key {KEY}"

        run_script(groq, clock, tmp_path)

        text = next(tmp_path.glob("*.json")).read_text(encoding="utf-8")
        assert KEY not in text
        assert "[redacted]" in text

    def test_budget_guard_stops_after_the_first_call(self, groq, clock, tmp_path):
        groq.tokens_per_call = 600  # four calls would use 2400 of 3600 a day

        code = run_script(groq, clock, tmp_path)

        assert code == 1
        assert groq.tts_count == 1
        assert groq.stt_count == 0
        stopped = saved(tmp_path)["stopped"]
        assert stopped["stage"] == "budget"
        assert "600" in stopped["message"]

    def test_budget_guard_does_not_stop_a_small_run(self, groq, clock, tmp_path):
        groq.tokens_per_call = 600

        code = run_script(groq, clock, tmp_path, ["--chunks", "1"])

        assert code == 0  # one call of 600 is under half of 3600


class TestOutput:
    def test_prints_a_summary_and_the_daily_usage_reminder(
        self, groq, clock, tmp_path, capsys
    ):
        run_script(groq, clock, tmp_path, ["--chunks", "1"])

        out = capsys.readouterr().out
        assert "Voice measurement" in out
        assert "usage page" in out
        assert KEY not in out


class TestAudioDiagnostics:
    def test_the_result_records_real_audio_length_and_loudness(
        self, groq, clock, tmp_path
    ):
        run_script(groq, clock, tmp_path, ["--chunks", "1"])

        audio = saved(tmp_path)["tts"][0]["audio"]
        assert audio["seconds"] == pytest.approx(2.0)
        assert audio["peak"] > 0.2
        assert audio["leading_silence_s"] == pytest.approx(0.0, abs=0.01)

    def test_a_streaming_header_does_not_inflate_the_length(
        self, groq, clock, tmp_path
    ):
        groq.wav_factory = lambda: make_wav(3.0, streaming_header=True)

        run_script(groq, clock, tmp_path, ["--chunks", "1"])

        document = saved(tmp_path)
        assert document["tts"][0]["audio"]["seconds"] == pytest.approx(3.0)
        assert document["stt"][0]["real_time_factor"] == pytest.approx(
            0.4 / 3.0, abs=0.01
        )

    def test_tokens_per_character_is_recorded(self, groq, clock, tmp_path):
        groq.tokens_per_call = 136

        run_script(groq, clock, tmp_path, ["--chunks", "1"])

        record = saved(tmp_path)["tts"][0]
        assert record["tokens_used_estimate"] == 136
        assert record["tokens_per_char"] == round(136 / len(mv.SENTENCES[0]), 2)

    def test_a_nearly_silent_clip_is_flagged_and_not_blamed_on_whisper(
        self, groq, clock, tmp_path, capsys
    ):
        # First real runs: Orpheus returned near-silent clips; Whisper said "you".
        groq.wav_factory = lambda: make_wav(2.0, level=0)
        groq.transcript_for = lambda text: " you"

        run_script(groq, clock, tmp_path, ["--chunks", "2"])

        summary = saved(tmp_path)["summary"]
        assert summary["defective_audio"] == 2
        assert summary["wrong_transcripts"] == 2
        assert summary["wrong_on_good_audio"] == 0
        out = capsys.readouterr().out
        assert "nearly silent: 2 of 2" in out
        assert "nearly silent clip" in out

    def test_a_clip_with_sound_that_is_transcribed_wrongly_is_blamed_on_whisper(
        self, groq, clock, tmp_path
    ):
        groq.transcript_for = lambda text: " Thank you."

        run_script(groq, clock, tmp_path, ["--chunks", "1"])

        summary = saved(tmp_path)["summary"]
        assert summary["defective_audio"] == 0
        assert summary["wrong_on_good_audio"] == 1

    def test_wrong_transcripts_are_counted_and_listed(
        self, groq, clock, tmp_path, capsys
    ):
        groq.transcript_for = lambda text: " Thank you."

        run_script(groq, clock, tmp_path, ["--chunks", "2"])

        assert saved(tmp_path)["summary"]["wrong_transcripts"] == 2
        out = capsys.readouterr().out
        assert "Wrong transcripts: 2 of 2" in out
        assert "Thank you." in out
        assert "clip had sound" in out

    def test_good_transcripts_are_not_listed(self, groq, clock, tmp_path, capsys):
        run_script(groq, clock, tmp_path, ["--chunks", "2"])

        assert "Wrong transcripts: 0 of 2" in capsys.readouterr().out


class TestSaveAudio:
    def test_off_by_default(self, groq, clock, tmp_path):
        run_script(groq, clock, tmp_path, ["--chunks", "2"])

        assert list(tmp_path.glob("*.wav")) == []

    def test_saves_one_wav_per_sentence_when_asked(self, groq, clock, tmp_path):
        run_script(groq, clock, tmp_path, ["--chunks", "2", "--save-audio"])

        names = sorted(p.name for p in tmp_path.glob("*.wav"))
        assert names == [
            "voice_audio_20261125T120000Z_1.wav",
            "voice_audio_20261125T120000Z_2.wav",
        ]
        assert tmp_path.joinpath(names[0]).read_bytes()[:4] == b"RIFF"

    def test_dry_run_says_whether_audio_will_be_saved(
        self, groq, clock, tmp_path, capsys
    ):
        run_script(groq, clock, tmp_path, ["--dry-run", "--save-audio"])

        assert "Save audio    : yes" in capsys.readouterr().out
