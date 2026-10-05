"""Groq Whisper Speech-to-Text provider.

Uses the Groq Audio Transcriptions API which is OpenAI-Whisper-compatible:
  POST https://api.groq.com/openai/v1/audio/transcriptions

Groq does not expose per-word confidence scores in its standard response.
We derive a proxy confidence from two signals:
  1. ``avg_logprob``  — mean log-probability over tokens in verbose JSON mode.
     Range is (-∞, 0]; we map it to (0, 1] via exp().
  2. ``no_speech_prob`` — probability the segment is silence/noise.
     High values indicate low-quality audio.

If the verbose_json response is unavailable we fall back to a fixed
conservative confidence of 0.80 for any non-empty transcript, 0.0 for empty.

Reference:
  https://console.groq.com/docs/speech-text
"""

import io
import math
from typing import Any, Dict, List, Optional

import httpx

from backend.app.core.config import settings
from backend.app.core.exceptions import SpeechTranscriptionError
from backend.app.core.logging import logger
from backend.app.services.providers.stt_base import SpeechToTextProvider, TranscriptionResult

# Groq audio transcription endpoint
_GROQ_STT_URL = "https://api.groq.com/openai/v1/audio/transcriptions"

# Filename sent with the multipart upload; the extension tells Groq the codec.
_MIME_TO_EXT: Dict[str, str] = {
    "audio/ogg": "audio.ogg",
    "audio/ogg; codecs=opus": "audio.ogg",
    "audio/mpeg": "audio.mp3",
    "audio/mp4": "audio.mp4",
    "audio/mp4a-latm": "audio.mp4",
    "audio/wav": "audio.wav",
    "audio/x-wav": "audio.wav",
    "audio/webm": "audio.webm",
    "audio/flac": "audio.flac",
}
_DEFAULT_FILENAME = "audio.ogg"

# Languages that Groq Whisper handles well; used to nudge the model when
# language_hint is provided.
_SUPPORTED_LANGUAGE_HINTS = {"ur", "en", "hi", "ar", "fr", "de", "es", "pt"}


def _derive_confidence(segments: List[Dict[str, Any]]) -> float:
    """Derive a 0–1 confidence score from verbose_json segment data.

    Strategy:
      - avg_logprob close to 0 → high confidence (exp(0) = 1.0)
      - avg_logprob of -1 → ~0.37; of -2 → ~0.14
      - no_speech_prob > 0.5 on any segment pulls the score down sharply
    """
    if not segments:
        return 0.0

    log_probs: List[float] = []
    max_no_speech: float = 0.0

    for seg in segments:
        lp = seg.get("avg_logprob")
        nsp = seg.get("no_speech_prob", 0.0)
        if lp is not None:
            log_probs.append(float(lp))
        if nsp is not None:
            max_no_speech = max(max_no_speech, float(nsp))

    if not log_probs:
        # Segments present but no logprob data — use a conservative default
        base_conf = 0.75
    else:
        mean_lp = sum(log_probs) / len(log_probs)
        # Clamp mean log-prob to [-3, 0] before exponentiation so very noisy
        # audio doesn't push confidence below ~0.05
        clamped = max(-3.0, min(0.0, mean_lp))
        base_conf = math.exp(clamped)

    # Penalise when any segment has a high silence probability
    # no_speech_prob=0.9 → multiplier ~0.1
    silence_penalty = 1.0 - max_no_speech
    return round(base_conf * silence_penalty, 4)


class GroqWhisperProvider(SpeechToTextProvider):
    """Production STT provider backed by Groq's Whisper-large-v3 endpoint.

    Thread-safe and reusable; creates a fresh httpx client per call so it
    plays nicely with async contexts without holding open connections.
    """

    @property
    def provider_name(self) -> str:
        return "groq_whisper"

    async def transcribe(
        self,
        audio_bytes: bytes,
        mime_type: str = "audio/ogg",
        language_hint: Optional[str] = None,
    ) -> TranscriptionResult:
        """Send audio to Groq Whisper and return a TranscriptionResult.

        Raises:
            SpeechTranscriptionError: on auth failure (401), invalid audio
                (400), or unrecoverable network errors.
        """
        api_key = settings.GROQ_API_KEY
        if not api_key:
            # Dev/test fallback: return a mock result so the pipeline can be
            # tested without a live Groq API key.
            logger.warning(
                "GROQ_API_KEY not set — returning mock STT result (dev mode).",
            )
            return TranscriptionResult(
                text="[MOCK TRANSCRIPTION — set GROQ_API_KEY for real STT]",
                confidence=0.0,
                provider=self.provider_name,
            )

        model = settings.STT_GROQ_MODEL
        filename = _MIME_TO_EXT.get(mime_type.lower(), _DEFAULT_FILENAME)

        # Build multipart form data
        files = {"file": (filename, io.BytesIO(audio_bytes), mime_type)}
        data: Dict[str, Any] = {
            "model": model,
            "response_format": "verbose_json",   # needed to get segment logprobs
            "temperature": 0,                     # deterministic output
        }
        if language_hint and language_hint in _SUPPORTED_LANGUAGE_HINTS:
            data["language"] = language_hint

        headers = {"Authorization": f"Bearer {api_key}"}

        logger.info(
            "Sending audio to Groq Whisper.",
            extra={
                "model": model,
                "mime_type": mime_type,
                "bytes": len(audio_bytes),
                "language_hint": language_hint,
            },
        )

        async with httpx.AsyncClient(timeout=60.0) as client:
            try:
                resp = await client.post(
                    _GROQ_STT_URL,
                    headers=headers,
                    files=files,
                    data=data,
                )
            except httpx.RequestError as exc:
                raise SpeechTranscriptionError(
                    f"Network error reaching Groq STT API: {exc}",
                    details={"mime_type": mime_type},
                ) from exc

        # ---- error handling ------------------------------------------------
        if resp.status_code == 401:
            raise SpeechTranscriptionError(
                "Groq STT authentication failed — check GROQ_API_KEY.",
                details={"status_code": 401},
            )
        if resp.status_code == 400:
            raise SpeechTranscriptionError(
                f"Groq STT rejected audio: {resp.text}",
                details={"status_code": 400, "body": resp.text},
            )
        if resp.status_code >= 500:
            raise SpeechTranscriptionError(
                f"Groq STT server error {resp.status_code}: {resp.text}",
                details={"status_code": resp.status_code},
            )
        if resp.status_code >= 300:
            raise SpeechTranscriptionError(
                f"Unexpected Groq STT response {resp.status_code}: {resp.text}",
                details={"status_code": resp.status_code},
            )

        # ---- parse response ------------------------------------------------
        try:
            body: Dict[str, Any] = resp.json()
        except Exception as exc:
            raise SpeechTranscriptionError(
                "Groq STT returned non-JSON response.",
                details={"raw": resp.text[:200]},
            ) from exc

        transcript: str = body.get("text", "").strip()
        language: Optional[str] = body.get("language")
        duration: Optional[float] = body.get("duration")
        segments: List[Dict[str, Any]] = body.get("segments", [])

        # Derive confidence from segment logprobs when available
        if segments:
            confidence = _derive_confidence(segments)
        elif transcript:
            # verbose_json didn't include segments — use conservative default
            confidence = 0.80
        else:
            confidence = 0.0

        logger.info(
            "Groq Whisper transcription complete.",
            extra={
                "confidence": confidence,
                "language": language,
                "duration_secs": duration,
                "text_length": len(transcript),
            },
        )

        return TranscriptionResult(
            text=transcript,
            confidence=confidence,
            language=language,
            duration_secs=duration,
            provider=self.provider_name,
            raw_response=body,
        )
