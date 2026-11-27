"""Unit tests for wav_audio: reading Groq's streaming WAV header, the "does this clip
have sound" check, and joining clips.

All audio here is made in the tests (tones, silence, noise). The numbers in the
module come from four real Orpheus clips measured on 2026-11-25: the good ones had
sound in 65% to 67% of their samples and the two nearly silent ones in 1.3% and 1.4%.
"""

import io
import math
import random
import struct
import wave
from array import array

import pytest

from app.services import wav_audio
from app.services.wav_audio import (
    MIN_ACTIVE_SHARE,
    PAUSE_SECONDS,
    QUIET_LEVEL,
    WavClip,
    WavError,
    build_wav,
    join_clips,
    parse_wav,
)

RATE = 24000


def pcm_of(samples) -> bytes:
    return array("h", samples).tobytes()


def tone(seconds: float, amplitude: float = 0.5, rate: int = RATE) -> bytes:
    """A sine tone, which has sound in nearly every sample."""
    count = round(seconds * rate)
    return pcm_of(
        round(amplitude * 32767 * math.sin(2 * math.pi * 440 * i / rate))
        for i in range(count)
    )


def silence(seconds: float, rate: int = RATE) -> bytes:
    return b"\x00\x00" * round(seconds * rate)


def chunk(name: bytes, body: bytes, size: int | None = None) -> bytes:
    declared = len(body) if size is None else size
    return name + struct.pack("<I", declared) + body + (b"\x00" * (len(body) % 2))


def fmt_chunk(channels=1, rate=RATE, bits=16, tag=1) -> bytes:
    block = channels * bits // 8
    return chunk(
        b"fmt ", struct.pack("<HHIIHH", tag, channels, rate, rate * block, block, bits)
    )


def orpheus_wav(pcm: bytes) -> bytes:
    """What Groq returns: a LIST chunk and a placeholder length for the audio."""
    return (
        b"RIFF"
        + struct.pack("<I", 0xFFFFFFFF)
        + b"WAVE"
        + fmt_chunk()
        + chunk(b"LIST", b"INFO" + b"ISFT\x04\x00\x00\x00Lavf\x00")
        + b"data"
        + struct.pack("<I", 0xFFFFFFFF)
        + pcm
    )


def clip(pcm: bytes, channels: int = 1, rate: int = RATE) -> WavClip:
    return WavClip(channels, rate, pcm)


# ── Reading a file ────────────────────────────────────────────────────────────


class TestParse:
    def test_reads_a_well_formed_file(self):
        parsed = parse_wav(build_wav(tone(1.0), 1, RATE))

        assert (parsed.channels, parsed.sample_rate) == (1, RATE)
        assert parsed.seconds == pytest.approx(1.0)
        assert parsed.pcm == tone(1.0)

    def test_reads_groqs_streaming_header(self):
        """The length field says about 25 hours; the real length is what arrived."""
        parsed = parse_wav(orpheus_wav(tone(7.2)))

        assert parsed.seconds == pytest.approx(7.2)
        assert parsed.sample_rate == RATE

    def test_a_length_of_zero_means_use_what_arrived(self):
        data = (
            b"RIFF\x00\x00\x00\x00WAVE"
            + fmt_chunk()
            + b"data"
            + struct.pack("<I", 0)
            + tone(0.5)
        )

        assert parse_wav(data).seconds == pytest.approx(0.5)

    def test_an_honest_length_is_respected(self):
        """Bytes after the audio (another chunk) are not audio."""
        audio = tone(0.5)
        data = (
            b"RIFF\x00\x00\x00\x00WAVE"
            + fmt_chunk()
            + chunk(b"data", audio)
            + chunk(b"LIST", b"INFO" + b"x" * 40)
        )

        parsed = parse_wav(data)

        assert parsed.pcm == audio

    def test_a_length_longer_than_the_data_is_cut_to_what_is_there(self):
        audio = tone(0.5)
        data = (
            b"RIFF\x00\x00\x00\x00WAVE"
            + fmt_chunk()
            + b"data"
            + struct.pack("<I", len(audio) + 1000)
            + audio
        )

        assert parse_wav(data).pcm == audio

    def test_a_half_sample_at_the_end_is_dropped(self):
        parsed = parse_wav(orpheus_wav(tone(0.1) + b"\x07"))

        assert parsed.pcm == tone(0.1)

    def test_stereo_length_counts_frames_not_samples(self):
        data = (
            b"RIFF\x00\x00\x00\x00WAVE"
            + fmt_chunk(channels=2)
            + chunk(b"data", pcm_of([1000] * (RATE * 2)))  # one second of two channels
        )

        parsed = parse_wav(data)

        assert parsed.channels == 2
        assert parsed.seconds == pytest.approx(1.0)

    def test_a_partial_stereo_frame_is_dropped(self):
        data = (
            b"RIFF\x00\x00\x00\x00WAVE"
            + fmt_chunk(channels=2)
            + chunk(b"data", pcm_of([1, 2, 3]))  # one whole frame and half of another
        )

        assert len(parse_wav(data).pcm) == 4

    def test_chunks_are_padded_to_an_even_size(self):
        """An odd-sized chunk before the audio is followed by a padding byte."""
        data = (
            b"RIFF\x00\x00\x00\x00WAVE"
            + fmt_chunk()
            + chunk(b"junk", b"abc")
            + chunk(b"data", tone(0.1))
        )

        assert parse_wav(data).pcm == tone(0.1)

    def test_a_file_with_no_audio_samples_is_a_clip_of_zero_seconds(self):
        parsed = parse_wav(build_wav(b"", 1, RATE))

        assert parsed.seconds == 0
        assert parsed.has_sound() is False

    @pytest.mark.parametrize(
        "data",
        [
            b"",
            b"RIFF",
            b"not a wav file at all",
            b"RIFF\x00\x00\x00\x00AVI " + b"\x00" * 40,
            b"ID3\x04" + b"\x00" * 100,  # an mp3
        ],
    )
    def test_something_that_is_not_a_wav_file_is_refused(self, data):
        with pytest.raises(WavError):
            parse_wav(data)

    def test_a_file_without_a_data_chunk_is_refused(self):
        with pytest.raises(WavError, match="no audio"):
            parse_wav(b"RIFF\x00\x00\x00\x00WAVE" + fmt_chunk())

    def test_audio_before_the_format_is_refused(self):
        data = b"RIFF\x00\x00\x00\x00WAVE" + chunk(b"data", tone(0.1)) + fmt_chunk()

        with pytest.raises(WavError, match="before its format"):
            parse_wav(data)

    def test_a_cut_off_format_chunk_is_refused(self):
        with pytest.raises(WavError, match="damaged"):
            parse_wav(b"RIFF\x00\x00\x00\x00WAVE" + b"fmt " + struct.pack("<I", 16))

    def test_a_format_chunk_that_is_too_small_is_refused(self):
        data = b"RIFF\x00\x00\x00\x00WAVE" + chunk(b"fmt ", b"\x01\x00\x01\x00")

        with pytest.raises(WavError, match="damaged"):
            parse_wav(data)

    @pytest.mark.parametrize(
        "format_chunk",
        [
            fmt_chunk(bits=8),  # 8-bit
            fmt_chunk(bits=24),
            fmt_chunk(bits=32, tag=3),  # 32-bit float
            fmt_chunk(tag=0xFFFE),  # extensible
        ],
    )
    def test_formats_other_than_16_bit_pcm_are_refused(self, format_chunk):
        data = b"RIFF\x00\x00\x00\x00WAVE" + format_chunk + chunk(b"data", b"\x00" * 8)

        with pytest.raises(WavError, match="16-bit PCM"):
            parse_wav(data)

    @pytest.mark.parametrize("channels,rate", [(0, RATE), (1, 0)])
    def test_a_format_with_no_channels_or_no_rate_is_refused(self, channels, rate):
        data = (
            b"RIFF\x00\x00\x00\x00WAVE"
            + fmt_chunk(channels=channels, rate=rate)
            + chunk(b"data", b"\x00" * 8)
        )

        with pytest.raises(WavError):
            parse_wav(data)

    def test_damaged_input_only_ever_raises_wav_error(self):
        """Every cut-off of a real-looking file, and random bytes, end in a clip or a
        WavError, never in another exception."""
        good = orpheus_wav(tone(0.05))
        for end in range(len(good) + 1):
            try:
                parse_wav(good[:end])
            except WavError:
                pass

        generator = random.Random(7)
        for _ in range(300):
            blob = bytes(
                generator.randrange(256) for _ in range(generator.randrange(80))
            )
            try:
                parse_wav(b"RIFF\x00\x00\x00\x00WAVE" + blob)
            except WavError:
                pass

    def test_wav_error_is_a_value_error(self):
        assert issubclass(WavError, ValueError)


# ── Does the clip have sound ──────────────────────────────────────────────────


class TestSound:
    def test_a_tone_has_sound(self):
        sound = clip(tone(2.0))

        assert sound.active_share() > 0.95
        assert sound.has_sound() is True

    def test_silence_has_no_sound(self):
        quiet = clip(silence(2.0))

        assert quiet.active_share() == 0.0
        assert quiet.has_sound() is False

    def test_an_empty_clip_has_no_sound(self):
        assert clip(b"").active_share() == 0.0
        assert clip(b"").has_sound() is False

    def test_very_quiet_noise_counts_as_silence(self):
        generator = random.Random(3)
        floor = round(QUIET_LEVEL * 32768) - 2  # just under the quiet level
        hiss = clip(pcm_of(generator.randint(-floor, floor) for _ in range(RATE)))

        assert hiss.active_share() == 0.0
        assert hiss.has_sound() is False

    def test_the_quiet_level_is_inclusive(self):
        """328 is 1% of full scale (327.68) rounded up, so it counts; 327 does not."""
        assert clip(pcm_of([328, -328])).active_share() == 1.0
        assert clip(pcm_of([327, -327])).active_share() == 0.0

    def test_the_quiet_level_can_be_changed_and_is_inclusive_there_too(self):
        """At 50% the line is exactly 16384, a whole number."""
        assert clip(pcm_of([16384, -16384])).active_share(0.5) == 1.0
        assert clip(pcm_of([16383, -16383])).active_share(0.5) == 0.0

    def test_a_clip_with_a_few_loud_clicks_has_no_sound(self):
        """Like the measured bad clip 4: it peaks at full scale, but only from clicks."""
        samples = [0] * (RATE * 7)
        for position in range(0, len(samples), 70):
            samples[position] = 32767 if position % 140 == 0 else -32768
        clicks = clip(pcm_of(samples))

        assert max(abs(s) for s in array("h", clicks.pcm)) == 32768
        assert clicks.active_share() < 0.02
        assert clicks.has_sound() is False

    def test_speech_like_audio_with_pauses_has_sound(self):
        """Tone and silence mixed about the way speech is: well above the minimum."""
        pattern = tone(0.3) + silence(0.1)

        assert clip(pattern * 10).has_sound() is True

    def test_the_minimum_share_is_inclusive(self):
        loud = [1000]
        assert clip(pcm_of(loud + [0] * 4)).has_sound() is True  # 1 in 5 = 0.2
        assert clip(pcm_of(loud + [0] * 9)).has_sound() is False  # 1 in 10 = 0.1

    def test_the_minimum_can_be_changed(self):
        half = clip(pcm_of([1000, 0] * 100))

        assert half.has_sound(0.5) is True
        assert half.has_sound(0.6) is False

    def test_the_limits_sit_between_the_measured_good_and_bad_clips(self):
        assert 0.014 < MIN_ACTIVE_SHARE < 0.65
        assert QUIET_LEVEL == 0.01

    def test_stereo_is_measured_over_all_samples(self):
        left_only = clip(pcm_of([1000, 0] * 100), channels=2)

        assert left_only.active_share() == 0.5

    def test_a_clip_read_from_groqs_file_keeps_its_loudness(self):
        assert parse_wav(orpheus_wav(tone(1.0))).has_sound() is True
        assert parse_wav(orpheus_wav(silence(1.0))).has_sound() is False


# ── Building a file ───────────────────────────────────────────────────────────


class TestBuild:
    def test_the_python_wave_module_can_read_the_result(self):
        data = build_wav(tone(0.5), 1, RATE)

        with wave.open(io.BytesIO(data)) as reader:
            assert reader.getnchannels() == 1
            assert reader.getframerate() == RATE
            assert reader.getsampwidth() == 2
            assert reader.getnframes() == RATE // 2
            assert reader.readframes(reader.getnframes()) == tone(0.5)

    def test_groqs_original_gives_the_wrong_length_and_ours_the_right_one(self):
        """Why the file is rebuilt: the streaming header's length is a placeholder."""

        def frames_claimed(data: bytes):
            try:
                with wave.open(io.BytesIO(data)) as reader:
                    return reader.getnframes()
            except (wave.Error, EOFError):
                return None

        original = orpheus_wav(tone(0.5))

        assert frames_claimed(original) != RATE // 2
        assert frames_claimed(parse_wav(original).to_bytes()) == RATE // 2

    def test_the_header_lengths_are_exact(self):
        pcm = tone(0.25)
        data = build_wav(pcm, 1, RATE)

        assert len(data) == 44 + len(pcm)
        assert struct.unpack("<I", data[4:8])[0] == 36 + len(pcm)
        assert struct.unpack("<I", data[40:44])[0] == len(pcm)

    def test_stereo_header_fields(self):
        data = build_wav(pcm_of([1, 2] * 10), 2, 16000)

        with wave.open(io.BytesIO(data)) as reader:
            assert reader.getnchannels() == 2
            assert reader.getframerate() == 16000
            assert reader.getnframes() == 10
        assert struct.unpack("<I", data[28:32])[0] == 16000 * 4  # bytes a second
        assert struct.unpack("<H", data[32:34])[0] == 4  # bytes a frame

    def test_an_empty_clip_builds_a_valid_file(self):
        with wave.open(io.BytesIO(build_wav(b"", 1, RATE))) as reader:
            assert reader.getnframes() == 0

    def test_a_round_trip_gives_the_same_clip(self):
        original = clip(tone(0.3))

        assert parse_wav(original.to_bytes()) == original

    @pytest.mark.parametrize("channels,rate", [(0, RATE), (1, 0), (-1, RATE)])
    def test_a_bad_format_is_refused(self, channels, rate):
        with pytest.raises(WavError):
            build_wav(b"", channels, rate)

    def test_audio_too_long_for_a_wav_file_is_refused(self, monkeypatch):
        monkeypatch.setattr(wav_audio, "_MAX_DATA_BYTES", 10)

        with pytest.raises(WavError, match="too long"):
            build_wav(b"\x00" * 12, 1, RATE)

    def test_a_clip_must_be_whole_frames(self):
        with pytest.raises(WavError, match="whole number of frames"):
            WavClip(2, RATE, b"\x00\x00\x00")

    @pytest.mark.parametrize("channels,rate", [(0, RATE), (1, 0)])
    def test_a_clip_needs_a_channel_and_a_rate(self, channels, rate):
        with pytest.raises(WavError):
            WavClip(channels, rate, b"")


# ── Joining clips ─────────────────────────────────────────────────────────────


class TestJoin:
    def test_clips_are_joined_with_a_pause_between_them(self):
        first, second = clip(tone(1.0)), clip(tone(2.0))

        joined = join_clips([first, second])

        assert joined.seconds == pytest.approx(3.0 + PAUSE_SECONDS, abs=1 / RATE)

    def test_the_pause_is_silent_and_in_the_right_place(self):
        first, second = clip(tone(0.5)), clip(tone(0.5))

        joined = join_clips([first, second], pause_seconds=0.1)

        gap = round(0.1 * RATE) * 2
        assert joined.pcm[: len(first.pcm)] == first.pcm
        assert joined.pcm[len(first.pcm) : len(first.pcm) + gap] == b"\x00" * gap
        assert joined.pcm[len(first.pcm) + gap :] == second.pcm

    def test_three_clips_have_two_pauses(self):
        joined = join_clips([clip(tone(1.0))] * 3, pause_seconds=0.5)

        assert joined.seconds == pytest.approx(4.0)

    def test_one_clip_is_returned_without_a_pause(self):
        only = clip(tone(1.0))

        assert join_clips([only]).pcm == only.pcm

    def test_no_pause_when_it_is_zero_or_negative(self):
        pair = [clip(tone(0.5)), clip(tone(0.5))]

        assert join_clips(pair, pause_seconds=0).seconds == pytest.approx(1.0)
        assert join_clips(pair, pause_seconds=-3).seconds == pytest.approx(1.0)

    def test_stereo_pause_is_whole_frames(self):
        pair = [clip(pcm_of([5, 5] * 100), channels=2)] * 2

        joined = join_clips(pair, pause_seconds=0.01)

        assert len(joined.pcm) % joined.frame_bytes == 0
        assert joined.channels == 2

    def test_the_joined_clip_has_the_format_of_the_parts(self):
        joined = join_clips([clip(tone(0.1), rate=16000)] * 2)

        assert joined.sample_rate == 16000

    def test_clips_of_different_formats_cannot_be_joined(self):
        with pytest.raises(WavError, match="same format"):
            join_clips([clip(tone(0.1)), clip(tone(0.1, rate=16000), rate=16000)])
        with pytest.raises(WavError, match="same format"):
            join_clips([clip(tone(0.1)), clip(pcm_of([1, 2]), channels=2)])

    def test_nothing_to_join_is_an_error(self):
        with pytest.raises(WavError, match="no audio"):
            join_clips([])

    def test_joined_sound_is_still_sound_and_joined_silence_is_not(self):
        assert join_clips([clip(tone(1.0))] * 2).has_sound() is True
        assert join_clips([clip(silence(1.0))] * 2).has_sound() is False

    def test_the_joined_file_plays_in_the_wave_module(self):
        joined = join_clips([clip(tone(0.5)), clip(tone(0.5))])

        with wave.open(io.BytesIO(joined.to_bytes())) as reader:
            assert reader.getnframes() == len(joined.pcm) // 2
