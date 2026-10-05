"""Speech-to-Text provider abstraction.

Every STT implementation must satisfy this interface so the rest of the system
never depends on a specific vendor.  Swapping Groq for OpenAI Whisper or any
future provider requires only adding a new subclass and changing STT_PROVIDER
in the environment.

TranscriptionResult
-------------------
The canonical output of any STT call.

  text          — normalised transcript (may be empty on failure)
  confidence    — float 0.0–1.0; providers that don't expose raw confidence
                  should return a conservative fixed value (e.g. 0.85 on
                  success, 0.0 on error) rather than leave it None.
  language      — BCP-47 language tag detected by the model ("ur", "en", …)
  duration_secs — audio clip length in seconds; None if not reported
  provider      — string tag identifying which implementation produced this
  raw_response  — verbatim vendor response dict, kept for audit / debugging
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class TranscriptionResult:
    """Immutable value object representing the output of one STT call."""

    text: str
    confidence: float                       # 0.0 – 1.0
    language: Optional[str] = None         # BCP-47, e.g. "ur", "en", "hi"
    duration_secs: Optional[float] = None
    provider: str = "unknown"
    raw_response: Dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    @property
    def is_empty(self) -> bool:
        """True when the transcript is blank or only whitespace."""
        return not self.text.strip()

    def meets_confidence(self, threshold: float) -> bool:
        """True when confidence is at or above the given threshold."""
        return self.confidence >= threshold


class SpeechToTextProvider(ABC):
    """Abstract base for all Speech-to-Text provider implementations.

    Implementations must be:
      - stateless (one instance can handle concurrent calls)
      - async-safe (use httpx or equivalent async I/O)
      - forgiving (return a low-confidence result rather than raising for
        recoverable API errors; only raise SpeechTranscriptionError for
        unrecoverable failures like auth errors or corrupt audio)

    Contract
    --------
    ``transcribe()`` receives raw audio bytes plus an optional MIME type hint
    and returns a ``TranscriptionResult``.  It never returns ``None``.
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Short identifier, e.g. ``"groq_whisper"``."""

    @abstractmethod
    async def transcribe(
        self,
        audio_bytes: bytes,
        mime_type: str = "audio/ogg",
        language_hint: Optional[str] = None,
    ) -> TranscriptionResult:
        """Transcribe audio bytes and return a ``TranscriptionResult``.

        Args:
            audio_bytes:   Raw audio content (OGG/OPUS, MP4, MP3, WAV, etc.)
            mime_type:     MIME type of the audio, forwarded to the API.
            language_hint: Optional BCP-47 hint; providers may ignore it.

        Returns:
            A ``TranscriptionResult`` with at minimum ``text`` and
            ``confidence`` populated.

        Raises:
            SpeechTranscriptionError: For unrecoverable provider errors
                (e.g. 401 auth failure, 400 invalid audio format).
        """
