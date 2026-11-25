"""Measure Groq speech latency and what a call costs on the free plan.

Phase 8, task 1 (voice architecture decision). The Groq docs do not say how many
tokens a text-to-speech call uses against the free daily limit, or how long a call
takes for an interview-sized sentence. This script measures both with a few real
calls, so the decision in docs/architecture/voice.md rests on numbers.

Run from backend/, with GROQ_API_KEY in backend/.env or in the environment:

    python scripts/measure_voice.py --dry-run     # show the plan, make no calls
    python scripts/measure_voice.py               # about 8 real calls

What one run does, for each of up to four short interview-style sentences:

  1. Orpheus turns the sentence into speech (one call, at most 200 characters).
  2. Whisper turns that audio back into text (one call). The last sentence also asks
     for word timestamps, because the Groq docs contradict each other on whether
     that needs `verbose_json`.

It records the time of each call, how many tokens the speech call used, how long
the audio is, and how far the transcript is from the sentence (word error rate).
The sentences are spoken by a clean synthetic voice, so the error rate says nothing
about accents or noise: the voice evaluation set (task 16) covers that.

Free-tier safety
  - Calls to Orpheus are spaced at least 6.5 seconds apart (the limit is 10 a minute).
  - There are no retries. The first 429, rejected key, timeout or other error stops
    the run, and what was measured so far is still saved.
  - If the first Orpheus call suggests the whole run would use more than half of the
    daily token limit, the run stops after that call.

About tokens: Groq's rate-limit headers report the per-minute figure, not the daily
total. The tokens used by a call are therefore estimated from that figure, which is
a bucket that refills all the time. For the true daily total, look at the usage page
in the Groq console before and after the run.

Privacy: audio is kept in memory only and is not written to disk, unless you pass
--save-audio (the audio is synthetic, so this is safe, and it lets you listen to a
call that Whisper got wrong). The result file holds timings, counts, loudness
figures, the sentence transcripts and rate-limit headers. It never holds the API
key. Results go to data/eval/results/, which is git-ignored.

Exit codes: 0 finished, 1 stopped early (partial results saved), 2 not started.
"""

import argparse
import json
import os
import re
import statistics
import sys
import time
from array import array
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = REPO_ROOT / "data" / "eval" / "results"

API_BASE = "https://api.groq.com/openai/v1"
TTS_URL = f"{API_BASE}/audio/speech"
STT_URL = f"{API_BASE}/audio/transcriptions"

DEFAULT_TTS_MODEL = "canopylabs/orpheus-v1-english"
DEFAULT_STT_MODEL = "whisper-large-v3-turbo"
DEFAULT_VOICE = "daniel"

MAX_TTS_CHARS = 200  # Orpheus input limit per request (Groq docs, 2026-11-25)
MIN_GAP_SECONDS = 6.5  # free plan: 10 Orpheus requests a minute, with some margin
REQUEST_TIMEOUT_SECONDS = 60.0
FREE_TTS_TOKENS_PER_DAY = 3600  # Groq rate-limit page, free plan, 2026-11-25
BUDGET_SHARE = 0.5  # stop if the whole run would use more than this share of a day

# Synthetic interview-style sentences, written for this script. Each is at most
# MAX_TTS_CHARS characters, so one sentence is one Orpheus request.
SENTENCES = (
    "Tell me about a time you had to debug a production issue under pressure. "
    "What did you do first, and how did you decide what to try next?",
    "Walk me through how you would design a rate limiter for a public API. "
    "Start with the requirements you would ask about before drawing any boxes.",
    "Describe a disagreement with a teammate about a technical decision. "
    "How did you resolve it, and what would you do differently now?",
    "What is the difference between a process and a thread, and when would you "
    "pick one over the other for a service that handles many requests?",
)

NOTES = (
    "Groq's rate-limit headers report the per-minute token figure, not the daily "
    "total. tokens_used_estimate comes from the change in the per-minute figure. "
    "Check the usage page in the Groq console for the true daily total.",
    "The sentences are spoken by a clean synthetic voice. The word error rate says "
    "nothing about accents or background noise.",
)


# ── Pure helpers ──────────────────────────────────────────────────────────────


def normalise_words(text: str) -> list[str]:
    """Lower-case words, with punctuation dropped, for comparing two texts."""
    return re.findall(r"[a-z0-9']+", (text or "").lower())


def word_error_rate(reference: str, hypothesis: str) -> float:
    """Word-level edit distance divided by the number of reference words.

    Case and punctuation are ignored. With no reference words the rate is 0.0 when
    the hypothesis is empty too, and 1.0 otherwise.
    """
    ref, hyp = normalise_words(reference), normalise_words(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    previous = list(range(len(hyp) + 1))
    for i, ref_word in enumerate(ref, start=1):
        current = [i]
        for j, hyp_word in enumerate(hyp, start=1):
            cost = 0 if ref_word == hyp_word else 1
            current.append(
                min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost)
            )
        previous = current
    return previous[-1] / len(ref)


WRONG_WER = 0.2  # a transcript further off than this counts as wrong
DEFECTIVE_ACTIVE_SHARE = 0.2  # clips with less sound than this look empty
QUIET_LEVEL = 0.01  # a sample below 1% of full scale counts as silence


def _wav_pcm(data: bytes) -> tuple[tuple[int, int, int], bytes] | None:
    """(channels, sample rate, bits), and the audio bytes that are really there.

    Groq's Orpheus audio can carry a streaming header whose length field is a
    placeholder (about 2 billion frames), so the length is taken from the bytes
    received and not from the header.
    """
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return None
    position = 12
    fmt = None
    while position + 8 <= len(data):
        chunk = data[position : position + 4]
        size = int.from_bytes(data[position + 4 : position + 8], "little")
        body = position + 8
        if chunk == b"fmt " and body + 16 <= len(data):
            fmt = (
                int.from_bytes(data[body + 2 : body + 4], "little"),
                int.from_bytes(data[body + 4 : body + 8], "little"),
                int.from_bytes(data[body + 14 : body + 16], "little"),
            )
        elif chunk == b"data":
            if fmt is None:
                return None
            available = len(data) - body
            length = available if size == 0 or size > available else size
            return fmt, data[body : body + length]
        position = body + size + (size % 2)
    return None


def wav_info(data: bytes) -> dict[str, Any] | None:
    """Length and loudness of a WAV file, or None when it cannot be read.

    Loudness figures (16-bit audio only) help to tell a quiet or silent recording,
    which makes Whisper invent words, from a real transcription problem.
    """
    parsed = _wav_pcm(data)
    if parsed is None:
        return None
    (channels, rate, bits), pcm = parsed
    frame_bytes = channels * bits // 8
    if not rate or not frame_bytes:
        return None
    info: dict[str, Any] = {
        "seconds": len(pcm) / frame_bytes / rate,
        "sample_rate": rate,
        "channels": channels,
        "bits": bits,
    }
    if bits == 16 and len(pcm) >= 2:
        samples = array("h", pcm[: len(pcm) // 2 * 2])
        if sys.byteorder == "big":
            samples.byteswap()
        loud = [i for i, s in enumerate(samples) if abs(s) >= QUIET_LEVEL * 32768]
        per_second = rate * channels
        info["active_share"] = round(len(loud) / len(samples), 3)
        info["peak"] = round(max(abs(s) for s in samples) / 32768, 4)
        info["rms"] = round(
            (sum(s * s for s in samples) / len(samples)) ** 0.5 / 32768, 4
        )
        info["leading_silence_s"] = round(
            (loud[0] if loud else len(samples)) / per_second, 3
        )
        info["trailing_silence_s"] = round(
            (len(samples) - 1 - loud[-1] if loud else len(samples)) / per_second, 3
        )
    return info


def wav_seconds(data: bytes) -> float | None:
    """Length of a WAV file in seconds, or None when it cannot be read."""
    info = wav_info(data)
    return None if info is None else info["seconds"]


def audio_is_defective(info: Mapping[str, Any] | None) -> bool:
    """True when a clip holds almost no sound, so there is nothing to transcribe.

    From the first real runs: clips with speech had sound in over half of their
    samples; two Orpheus clips that Whisper answered with "." and "you" had it in
    only a few percent (median sample exactly zero).
    """
    share = (info or {}).get("active_share")
    return share is not None and share < DEFECTIVE_ACTIVE_SHARE


def pick_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """The rate-limit headers of a response, with lower-case names."""
    kept = {}
    for name, value in headers.items():
        lowered = name.lower()
        if lowered.startswith("x-ratelimit-") or lowered == "retry-after":
            kept[lowered] = value
    return kept


def _as_int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def estimate_tokens_used(
    previous: Mapping[str, str] | None,
    current: Mapping[str, str],
    seconds_since_previous: float | None,
) -> int | None:
    """Tokens a call used, from the per-minute token figure in the headers.

    The per-minute figure is a bucket that refills all the time (limit / 60 per
    second). Before this call the bucket held what the previous response left, plus
    the refill since then, up to the limit. With no previous call it was full.
    """
    remaining = _as_int(current.get("x-ratelimit-remaining-tokens"))
    limit = _as_int(current.get("x-ratelimit-limit-tokens"))
    if remaining is None or limit is None:
        return None
    before = float(limit)
    last = _as_int((previous or {}).get("x-ratelimit-remaining-tokens"))
    if last is not None and seconds_since_previous is not None:
        before = min(float(limit), last + limit / 60 * max(seconds_since_previous, 0))
    return max(0, round(before - remaining))


def redact(text: str, secret: str) -> str:
    """Remove the secret from a text, in case a server echoes it back."""
    return text.replace(secret, "[redacted]") if secret else text


def error_message(response: httpx.Response, api_key: str) -> str:
    """The error text of a failed response, short and without the key."""
    try:
        body = response.json()
        message = body["error"]["message"] if isinstance(body, dict) else str(body)
    except (ValueError, KeyError, TypeError):
        message = response.text
    return redact(str(message), api_key)[:300]


def hint_for(status: int | None, message: str) -> str:
    if status == 401:
        return "The key was rejected. Check GROQ_API_KEY."
    if status == 429:
        return (
            "A rate limit or the daily quota was reached. Wait for the time in "
            "retry-after (or until tomorrow) and run again."
        )
    if status in (400, 403) and "terms" in message.lower():
        return "Accept the model's terms in the Groq console, then run again."
    return ""


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 3) if values else None


def _max(values: list[float]) -> float | None:
    return round(max(values), 3) if values else None


def summarise(tts: list[dict], stt: list[dict]) -> dict[str, Any]:
    tokens = [r["tokens_used_estimate"] for r in tts if r["tokens_used_estimate"]]
    words = [r for r in stt if r["word_timestamps_requested"]]
    return {
        "tts_calls": len(tts),
        "stt_calls": len(stt),
        "tts_latency_s": {
            "median": _median([r["latency_s"] for r in tts]),
            "max": _max([r["latency_s"] for r in tts]),
        },
        "stt_latency_s": {
            "median": _median([r["latency_s"] for r in stt]),
            "max": _max([r["latency_s"] for r in stt]),
        },
        "stt_real_time_factor_median": _median(
            [r["real_time_factor"] for r in stt if r["real_time_factor"] is not None]
        ),
        "wer": {
            "mean": (round(statistics.mean(r["wer"] for r in stt), 3) if stt else None),
            "max": _max([r["wer"] for r in stt]),
        },
        "tokens_used_estimate_median": _median(tokens),
        "tokens_per_char_median": _median(
            [r["tokens_per_char"] for r in tts if r["tokens_per_char"] is not None]
        ),
        "audio_seconds_median": _median(
            [r["audio"]["seconds"] for r in tts if r.get("audio")]
        ),
        "defective_audio": sum(1 for r in tts if audio_is_defective(r.get("audio"))),
        "wrong_transcripts": sum(1 for r in stt if r["wer"] > WRONG_WER),
        "wrong_on_good_audio": sum(
            1 for r in stt if r["wer"] > WRONG_WER and not r["audio_defective"]
        ),
        "word_timestamps": (
            {"present": all(r["has_words"] for r in words), "count": words[-1]["words"]}
            if words
            else None
        ),
    }


# ── Groq calls ────────────────────────────────────────────────────────────────


class StopRun(Exception):
    """A call failed in a way that ends the run. Nothing is retried."""

    def __init__(
        self,
        stage: str,
        status: int | None,
        message: str,
        headers: dict[str, str] | None = None,
    ):
        super().__init__(message)
        self.info = {
            "stage": stage,
            "status": status,
            "message": message,
            "hint": hint_for(status, message),
            "headers": headers or {},
        }


def _send(client: httpx.Client, url: str, api_key: str, stage: str, **kwargs):
    try:
        response = client.post(
            url, headers={"Authorization": f"Bearer {api_key}"}, **kwargs
        )
    except httpx.TimeoutException:
        raise StopRun(stage, None, "The request timed out.") from None
    except httpx.HTTPError as exc:
        raise StopRun(stage, None, f"Network error: {type(exc).__name__}.") from None
    if response.status_code != 200:
        raise StopRun(
            stage,
            response.status_code,
            error_message(response, api_key),
            pick_headers(response.headers),
        )
    return response


def speak(client, api_key, model, voice, text, clock):
    """One Orpheus call. Returns (wav bytes, seconds taken, rate-limit headers)."""
    started = clock()
    response = _send(
        client,
        TTS_URL,
        api_key,
        "tts",
        json={
            "model": model,
            "input": text,
            "voice": voice,
            "response_format": "wav",
        },
    )
    return response.content, clock() - started, pick_headers(response.headers)


def transcribe(client, api_key, model, audio, with_words, clock):
    """One Whisper call. Returns (json body, seconds taken)."""
    data = {"model": model, "language": "en", "temperature": "0"}
    if with_words:
        data["response_format"] = "verbose_json"
        data["timestamp_granularities[]"] = "word"
    else:
        data["response_format"] = "json"
    started = clock()
    response = _send(
        client,
        STT_URL,
        api_key,
        "stt",
        files={"file": ("sample.wav", audio, "audio/wav")},
        data=data,
    )
    elapsed = clock() - started
    try:
        body = response.json()
    except ValueError:
        body = {}
    return (body if isinstance(body, dict) else {}), elapsed


# ── The run ───────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Measure Groq Orpheus and Whisper latency on the free plan."
    )
    parser.add_argument(
        "--chunks",
        type=int,
        default=len(SENTENCES),
        choices=range(1, len(SENTENCES) + 1),
        metavar=f"1-{len(SENTENCES)}",
        help="how many sentences to run (default: all)",
    )
    parser.add_argument("--tts-model", default=DEFAULT_TTS_MODEL)
    parser.add_argument("--stt-model", default=DEFAULT_STT_MODEL)
    parser.add_argument("--voice", default=DEFAULT_VOICE)
    parser.add_argument(
        "--dry-run", action="store_true", help="show the plan and make no calls"
    )
    parser.add_argument(
        "--save-audio",
        action="store_true",
        help="also save each synthetic WAV next to the result file, to listen to it",
    )
    return parser


def plan_text(args, sentences, results_dir: Path, key_present: bool) -> str:
    calls = len(sentences) * 2
    lines = [
        "Voice measurement plan (dry run, no calls made)",
        f"  Orpheus model : {args.tts_model} (voice {args.voice})",
        f"  Whisper model : {args.stt_model}",
        f"  Calls         : {len(sentences)} speech + {len(sentences)} transcription"
        f" = {calls}",
        f"  Pacing        : at least {MIN_GAP_SECONDS}s between Orpheus calls",
        "  Stops at      : the first 429, rejected key, timeout or error (no retries)",
        f"  Budget guard  : stops if the run would use over {int(BUDGET_SHARE * 100)}%"
        f" of {FREE_TTS_TOKENS_PER_DAY} tokens a day",
        f"  Results       : {results_dir}",
        f"  Save audio    : {'yes' if args.save_audio else 'no'}",
        f"  GROQ_API_KEY  : {'found' if key_present else 'NOT found'}",
        "  Sentences:",
    ]
    for i, text in enumerate(sentences, start=1):
        lines.append(f"    {i}. ({len(text)} chars) {text}")
    return "\n".join(lines)


def _save(results_dir: Path, stamp: str, document: dict[str, Any]) -> Path:
    path = Path(results_dir) / f"voice_latency_{stamp}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, ensure_ascii=False), "utf-8")
    return path


def _print_summary(document: dict[str, Any], path: Path) -> None:
    summary = document["summary"]
    print("\nVoice measurement")
    print(f"  Speech calls   : {summary['tts_calls']}")
    print(f"  Speech latency : {summary['tts_latency_s']} seconds")
    print(f"  Whisper calls  : {summary['stt_calls']}")
    print(f"  Whisper latency: {summary['stt_latency_s']} seconds")
    print(
        f"  Real-time factor (Whisper time / audio time): "
        f"{summary['stt_real_time_factor_median']}"
    )
    print(f"  Word error rate: {summary['wer']}")
    print(
        f"  Tokens per speech call (estimate): "
        f"{summary['tokens_used_estimate_median']}"
    )
    print(f"  Word timestamps: {summary['word_timestamps']}")
    print(f"  Tokens per character (estimate): {summary['tokens_per_char_median']}")
    print(f"  Audio length per sentence (median): {summary['audio_seconds_median']} s")
    print(
        f"  Orpheus clips that are nearly silent: {summary['defective_audio']} of "
        f"{summary['tts_calls']}"
    )
    print(
        f"  Wrong transcripts: {summary['wrong_transcripts']} of {summary['stt_calls']}"
        f" ({summary['wrong_on_good_audio']} on clips that had sound)"
    )
    for record in document["stt"]:
        if record["wer"] > WRONG_WER:
            audio = (document["tts"][record["index"]].get("audio")) or {}
            print(
                f"    sentence {record['index'] + 1}: error rate {record['wer']}, "
                f"heard {record['transcript']!r}, "
                f"{'nearly silent clip' if record['audio_defective'] else 'clip had sound'}"
                f" (sound in {audio.get('active_share')} of samples, "
                f"peak {audio.get('peak')})"
            )
    stopped = document["stopped"]
    if stopped:
        print(f"\nSTOPPED at {stopped['stage']}: {stopped['message']}")
        if stopped["hint"]:
            print(f"  {stopped['hint']}")
    print(f"\nSaved: {path}")
    print("Check the Groq console usage page for the true daily token total.")


def run(
    argv: list[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.perf_counter,
    now: Callable[[], datetime] | None = None,
    results_dir: Path = RESULTS_DIR,
) -> int:
    args = build_parser().parse_args(argv)
    env = os.environ if env is None else env
    sentences = SENTENCES[: args.chunks]

    if args.dry_run:
        print(plan_text(args, sentences, results_dir, bool(env.get("GROQ_API_KEY"))))
        return 0

    api_key = (env.get("GROQ_API_KEY") or "").strip()
    if not api_key:
        print(
            "GROQ_API_KEY is not set. Put it in backend/.env or export it, then "
            "run again.",
            file=sys.stderr,
        )
        return 2

    moment = (now or (lambda: datetime.now(timezone.utc)))()
    stamp = moment.strftime("%Y%m%dT%H%M%SZ")
    own_client = client is None
    http = client or httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS)

    tts_records: list[dict] = []
    stt_records: list[dict] = []
    stopped: dict | None = None
    previous_headers: dict[str, str] | None = None
    previous_end: float | None = None

    try:
        for index, text in enumerate(sentences):
            if previous_end is not None:
                wait = MIN_GAP_SECONDS - (clock() - previous_end)
                if wait > 0:
                    sleep(wait)

            audio, tts_seconds, headers = speak(
                http, api_key, args.tts_model, args.voice, text, clock
            )
            ended = clock()
            tokens = estimate_tokens_used(
                previous_headers,
                headers,
                None if previous_end is None else ended - previous_end,
            )
            audio_info = wav_info(audio)
            audio_seconds = None if audio_info is None else audio_info["seconds"]
            if args.save_audio:
                results_dir.mkdir(parents=True, exist_ok=True)
                (results_dir / f"voice_audio_{stamp}_{index + 1}.wav").write_bytes(
                    audio
                )
            tts_records.append(
                {
                    "index": index,
                    "chars": len(text),
                    "latency_s": round(tts_seconds, 3),
                    "audio_s": (
                        None if audio_seconds is None else round(audio_seconds, 3)
                    ),
                    "bytes": len(audio),
                    "audio": audio_info,
                    "tokens_used_estimate": tokens,
                    "tokens_per_char": (
                        None if tokens is None else round(tokens / len(text), 2)
                    ),
                    "headers": headers,
                }
            )
            previous_headers, previous_end = headers, ended

            if (
                index == 0
                and tokens is not None
                and tokens * len(sentences) > FREE_TTS_TOKENS_PER_DAY * BUDGET_SHARE
            ):
                stopped = {
                    "stage": "budget",
                    "status": None,
                    "message": (
                        f"One speech call used about {tokens} tokens, so {len(sentences)}"
                        f" calls would use over {int(BUDGET_SHARE * 100)}% of the "
                        f"{FREE_TTS_TOKENS_PER_DAY} tokens a day on the free plan."
                    ),
                    "hint": "Run with --chunks 1, or run again tomorrow.",
                    "headers": {},
                }
                break

            with_words = index == len(sentences) - 1
            body, stt_seconds = transcribe(
                http, api_key, args.stt_model, audio, with_words, clock
            )
            transcript = str(body.get("text") or "")
            words = body.get("words")
            stt_records.append(
                {
                    "index": index,
                    "latency_s": round(stt_seconds, 3),
                    "audio_s": (
                        None if audio_seconds is None else round(audio_seconds, 3)
                    ),
                    "real_time_factor": (
                        round(stt_seconds / audio_seconds, 3) if audio_seconds else None
                    ),
                    "audio_defective": audio_is_defective(audio_info),
                    "wer": round(word_error_rate(text, transcript), 3),
                    "transcript": transcript,
                    "word_timestamps_requested": with_words,
                    "has_words": isinstance(words, list) and len(words) > 0,
                    "words": len(words) if isinstance(words, list) else 0,
                }
            )
    except StopRun as stop:
        stopped = stop.info
    finally:
        if own_client:
            http.close()

    document = {
        "kind": "voice_latency",
        "version": 1,
        "created": moment.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "settings": {
            "tts_model": args.tts_model,
            "stt_model": args.stt_model,
            "voice": args.voice,
            "chunks": len(sentences),
            "min_gap_seconds": MIN_GAP_SECONDS,
        },
        "sentence_chars": [len(s) for s in sentences],
        "tts": tts_records,
        "stt": stt_records,
        "summary": summarise(tts_records, stt_records),
        "stopped": stopped,
        "notes": list(NOTES),
    }
    path = _save(results_dir, stamp, document)
    _print_summary(document, path)
    return 1 if stopped else 0


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv()
    sys.exit(run())


if __name__ == "__main__":
    main()
