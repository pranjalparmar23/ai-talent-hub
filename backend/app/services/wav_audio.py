"""Read, check and join WAV audio (16-bit PCM).

Phase 8, task 5. Groq's text-to-speech (Orpheus) returns WAV audio with two problems
that the speak endpoint must handle itself:

  1. The header is a streaming header. The length of the audio is a placeholder
     (0xFFFFFFFF, about 25 hours) and there is an extra LIST chunk, so the length is
     taken from the bytes that were received, never from the header. Some players and
     decoders also refuse such a file, so `WavClip.to_bytes()` writes a correct one.
  2. About half of the clips are nearly silent although they have the right length,
     the right header and full cost. Measured on 2026-11-25 on four clips: good clips
     had sound in 65% to 67% of their samples, the two bad ones in 1.3% and 1.4%. The
     loudest sample is no help (one bad clip peaks at full scale, from clicks), so
     the check is the share of samples that are not quiet: `WavClip.has_sound()`.

Only 16-bit PCM is supported, which is what Orpheus returns. Anything else raises
`WavError`, and so does any damaged input: parsing never raises another error type.
"""

import struct
import sys
from array import array
from dataclasses import dataclass
from typing import Sequence

# A sample below 1% of full scale counts as silence.
QUIET_LEVEL = 0.01
# A clip with sound in fewer samples than this is treated as empty. The measured good
# clips are at 0.65 and above and the bad ones at 0.014 and below.
MIN_ACTIVE_SHARE = 0.2
# Silence put between joined clips, so two sentences do not run together.
PAUSE_SECONDS = 0.15

_PCM = 1  # WAVE_FORMAT_PCM
_BYTES_PER_SAMPLE = 2
_MAX_DATA_BYTES = 0xFFFFFFFF - 36


class WavError(ValueError):
    """The audio is not a readable 16-bit PCM WAV file (or cannot be built)."""


@dataclass(frozen=True)
class WavClip:
    """Raw samples and their format."""

    channels: int
    sample_rate: int
    pcm: bytes  # 16-bit little-endian samples, a whole number of frames

    def __post_init__(self) -> None:
        if self.channels < 1 or self.sample_rate < 1:
            raise WavError("The channel count and sample rate must be positive.")
        if len(self.pcm) % self.frame_bytes:
            raise WavError("The audio must be a whole number of frames.")

    @property
    def frame_bytes(self) -> int:
        return self.channels * _BYTES_PER_SAMPLE

    @property
    def seconds(self) -> float:
        return len(self.pcm) / self.frame_bytes / self.sample_rate

    def active_share(self, quiet_level: float = QUIET_LEVEL) -> float:
        """Share of samples that are not quiet, from 0.0 to 1.0 (0.0 when empty)."""
        if not self.pcm:
            return 0.0
        samples = array("h", self.pcm)
        if sys.byteorder == "big":
            samples.byteswap()
        floor = quiet_level * 32768
        loud = sum(1 for sample in samples if abs(sample) >= floor)
        return loud / len(samples)

    def has_sound(self, min_share: float = MIN_ACTIVE_SHARE) -> bool:
        """False for a clip that is (nearly) silent, however long it is."""
        return self.active_share() >= min_share

    def to_bytes(self) -> bytes:
        """The clip as a well-formed WAV file (correct lengths, no extra chunks)."""
        return build_wav(self.pcm, self.channels, self.sample_rate)


def parse_wav(data: bytes) -> WavClip:
    """Read a WAV file. The audio length comes from the bytes that are there."""
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise WavError("This is not a WAV file.")

    position = 12
    fmt: tuple[int, int] | None = None  # channels, sample rate
    while position + 8 <= len(data):
        chunk = data[position : position + 4]
        size = int.from_bytes(data[position + 4 : position + 8], "little")
        body = position + 8

        if chunk == b"fmt ":
            if size < 16 or body + 16 > len(data):
                raise WavError("The WAV format chunk is damaged.")
            tag, channels, rate, _, _, bits = struct.unpack(
                "<HHIIHH", data[body : body + 16]
            )
            if tag != _PCM or bits != 16:
                raise WavError("Only 16-bit PCM WAV audio is supported.")
            if channels < 1 or rate < 1:
                raise WavError("The WAV format chunk is damaged.")
            fmt = (channels, rate)
        elif chunk == b"data":
            if fmt is None:
                raise WavError("The WAV audio comes before its format.")
            available = len(data) - body
            # A streaming header uses a placeholder (0xFFFFFFFF) or 0 for the length.
            length = available if size == 0 or size > available else size
            channels, rate = fmt
            length -= length % (channels * _BYTES_PER_SAMPLE)  # whole frames only
            return WavClip(channels, rate, bytes(data[body : body + length]))

        position = body + size + (size % 2)  # chunks are padded to an even size

    raise WavError("The WAV file has no audio.")


def build_wav(pcm: bytes, channels: int, sample_rate: int) -> bytes:
    """A WAV file around 16-bit PCM samples, with a correct header."""
    if channels < 1 or sample_rate < 1:
        raise WavError("The channel count and sample rate must be positive.")
    if len(pcm) > _MAX_DATA_BYTES:
        raise WavError("The audio is too long for a WAV file.")
    block = channels * _BYTES_PER_SAMPLE
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + len(pcm),
        b"WAVE",
        b"fmt ",
        16,
        _PCM,
        channels,
        sample_rate,
        sample_rate * block,
        block,
        16,
        b"data",
        len(pcm),
    )
    return header + pcm


def join_clips(
    clips: Sequence[WavClip], pause_seconds: float = PAUSE_SECONDS
) -> WavClip:
    """One clip made of several, with `pause_seconds` of silence between them."""
    if not clips:
        raise WavError("There is no audio to join.")
    first = clips[0]
    for clip in clips[1:]:
        if (clip.channels, clip.sample_rate) != (first.channels, first.sample_rate):
            raise WavError("The clips do not have the same format.")

    gap = b"\x00" * (
        round(max(pause_seconds, 0) * first.sample_rate) * first.frame_bytes
    )
    return WavClip(
        first.channels, first.sample_rate, gap.join(clip.pcm for clip in clips)
    )
