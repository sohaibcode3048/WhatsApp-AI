"""Speech processing service — MODULE 07.

Responsibilities:
  1. Download audio binary from Meta's media endpoint (authenticated).
  2. Enforce max-size guard before sending to STT API.
  3. Dispatch bytes to the configured SpeechToTextProvider.
  4. Evaluate confidence; raise LowConfidenceTranscriptionError when the
     transcript is not reliable enough for financial/inventory operations.
  5. Return a ready-to-use TranscriptionResult to the caller.

Provider selection
------------------
The active provider is determined by ``settings.STT_PROVIDER``:

  "groq"  → GroqWhisperProvider  (default)
  "mock"  → MockSpeechProvider   (tests / CI without API keys)

Adding a new provider:
  1. Subclass SpeechToTextProvider in services/providers/
  2. Register it in ``_build_provider()`` below.

Design note
-----------
SpeechService is intentionally stateless: every public method receives all
required data as arguments and creates its own httpx client.  This keeps it
safe for concurrent async use without any shared mutable state.
"""

from typing import Optional

import httpx

from backend.app.core.config import settings
from backend.app.core.exceptions import (
    LowConfidenceTranscriptionError,
    SpeechTranscriptionError,
)
from backend.app.core.logging import logger
from backend.app.services.providers.stt_base import SpeechToTextProvider, TranscriptionResult

# ---------------------------------------------------------------------------
# Provider factory
# ---------------------------------------------------------------------------


def _build_provider(provider_name: str) -> SpeechToTextProvider:
    """Instantiate the STT provider named by the settings key.

    Raises ValueError for unknown provider names so configuration mistakes
    surface immediately at startup rather than silently at runtime.
    """
    name = provider_name.lower()

    if name == "groq":
        from backend.app.services.providers.groq_whisper import GroqWhisperProvider
        return GroqWhisperProvider()

    if name == "mock":
        from backend.app.services.speech_service import MockSpeechProvider
        return MockSpeechProvider()

    raise ValueError(
        f"Unknown STT_PROVIDER '{provider_name}'. "
        "Supported values: 'groq', 'mock'."
    )


# ---------------------------------------------------------------------------
# Mock provider (testing / CI)
# ---------------------------------------------------------------------------


class MockSpeechProvider(SpeechToTextProvider):
    """In-process STT stub for unit tests.

    Returns a configurable transcript so tests never hit external APIs.
    Instantiate and set attributes directly:

        provider = MockSpeechProvider()
        provider.mock_text = "Ali ka 2000 udhar likh do"
        provider.mock_confidence = 0.95
    """

    def __init__(self) -> None:
        self.mock_text: str = "[MOCK_TRANSCRIPT]"
        self.mock_confidence: float = 0.90
        self.mock_language: Optional[str] = "ur"
        self.mock_duration: Optional[float] = 3.0
        self.should_raise: Optional[Exception] = None  # set to force an exception

    @property
    def provider_name(self) -> str:
        return "mock"

    async def transcribe(
        self,
        audio_bytes: bytes,
        mime_type: str = "audio/ogg",
        language_hint: Optional[str] = None,
    ) -> TranscriptionResult:
        if self.should_raise is not None:
            raise self.should_raise
        return TranscriptionResult(
            text=self.mock_text,
            confidence=self.mock_confidence,
            language=self.mock_language,
            duration_secs=self.mock_duration,
            provider=self.provider_name,
        )


# ---------------------------------------------------------------------------
# Main service
# ---------------------------------------------------------------------------


class SpeechService:
    """Orchestrates audio download → STT transcription → confidence check.

    Usage::

        service = SpeechService()
        result = await service.process_audio_message(
            media_id="abc123",
            mime_type="audio/ogg",
            access_token="Bearer ...",
        )
        # result.text contains the transcript
    """

    # Max bytes we'll forward to the STT API (Meta enforces 16 MB; we add
    # a small buffer to match settings.STT_MAX_AUDIO_SIZE_MB).
    _MAX_BYTES: int = settings.STT_MAX_AUDIO_SIZE_MB * 1024 * 1024

    def __init__(self, provider: Optional[SpeechToTextProvider] = None) -> None:
        """Accept an injected provider for testability, or build from settings."""
        self._provider: SpeechToTextProvider = (
            provider if provider is not None
            else _build_provider(settings.STT_PROVIDER)
        )

    @property
    def provider(self) -> SpeechToTextProvider:
        """Expose active provider for introspection / testing."""
        return self._provider

    # ------------------------------------------------------------------
    # Step 1: Download audio from Meta
    # ------------------------------------------------------------------

    async def download_audio(
        self,
        media_id: str,
        access_token: str,
    ) -> tuple[bytes, str]:
        """Download audio binary from Meta's Graph API.

        Flow:
          1. GET /v{version}/{media_id} → resolve CDN URL + mime_type
          2. GET {url}                  → download raw bytes

        Returns:
            (audio_bytes, mime_type)

        Raises:
            SpeechTranscriptionError: on any HTTP error or network failure.
        """
        api_version = settings.WHATSAPP_API_VERSION
        meta_url = f"https://graph.facebook.com/{api_version}/{media_id}"
        auth_header = {"Authorization": f"Bearer {access_token}"}

        async with httpx.AsyncClient(timeout=30.0) as client:
            # Step 1 — resolve media metadata
            try:
                meta_resp = await client.get(meta_url, headers=auth_header)
            except httpx.RequestError as exc:
                raise SpeechTranscriptionError(
                    f"Network error fetching Meta media metadata: {exc}",
                    details={"media_id": media_id},
                ) from exc

            if meta_resp.status_code == 401:
                raise SpeechTranscriptionError(
                    "Meta media download auth failed — check WHATSAPP_ACCESS_TOKEN.",
                    details={"media_id": media_id, "status_code": 401},
                )
            if meta_resp.status_code != 200:
                raise SpeechTranscriptionError(
                    f"Meta media metadata request failed: {meta_resp.status_code}",
                    details={"media_id": media_id, "status_code": meta_resp.status_code},
                )

            try:
                meta_json = meta_resp.json()
            except Exception as exc:
                raise SpeechTranscriptionError(
                    "Meta media metadata response was not valid JSON.",
                    details={"media_id": media_id},
                ) from exc

            cdn_url: Optional[str] = meta_json.get("url")
            mime_type: str = meta_json.get("mime_type", "audio/ogg")

            if not cdn_url:
                raise SpeechTranscriptionError(
                    "Meta media metadata response missing 'url' field.",
                    details={"media_id": media_id, "response": meta_json},
                )

            # Step 2 — download raw audio bytes
            try:
                audio_resp = await client.get(cdn_url, headers=auth_header)
            except httpx.RequestError as exc:
                raise SpeechTranscriptionError(
                    f"Network error downloading audio from CDN: {exc}",
                    details={"media_id": media_id},
                ) from exc

            if audio_resp.status_code != 200:
                raise SpeechTranscriptionError(
                    f"Audio CDN download failed: {audio_resp.status_code}",
                    details={"media_id": media_id, "status_code": audio_resp.status_code},
                )

        audio_bytes = audio_resp.content
        logger.info(
            "Audio downloaded from Meta.",
            extra={
                "media_id": media_id,
                "mime_type": mime_type,
                "bytes": len(audio_bytes),
            },
        )
        return audio_bytes, mime_type

    # ------------------------------------------------------------------
    # Step 2: Size guard
    # ------------------------------------------------------------------

    def _check_size(self, audio_bytes: bytes, media_id: str) -> None:
        """Raise SpeechTranscriptionError if audio exceeds the size limit."""
        size = len(audio_bytes)
        if size > self._MAX_BYTES:
            raise SpeechTranscriptionError(
                f"Audio file too large: {size / (1024*1024):.1f} MB "
                f"(limit {settings.STT_MAX_AUDIO_SIZE_MB} MB).",
                details={"media_id": media_id, "bytes": size},
            )

    # ------------------------------------------------------------------
    # Step 3: Transcribe
    # ------------------------------------------------------------------

    async def transcribe(
        self,
        audio_bytes: bytes,
        mime_type: str = "audio/ogg",
        language_hint: Optional[str] = None,
    ) -> TranscriptionResult:
        """Send bytes to the configured STT provider and return the result.

        Does NOT check confidence — that is done in ``process_audio_message``.
        Useful when the caller wants the raw result without the confidence gate.
        """
        return await self._provider.transcribe(
            audio_bytes=audio_bytes,
            mime_type=mime_type,
            language_hint=language_hint,
        )

    # ------------------------------------------------------------------
    # Step 4: Confidence gate
    # ------------------------------------------------------------------

    def _check_confidence(self, result: TranscriptionResult) -> None:
        """Raise LowConfidenceTranscriptionError when the transcript is unreliable.

        Called after transcription.  Financial and inventory mutations must
        NOT proceed when this raises — the pipeline should ask the sender
        to resend the message as text.
        """
        threshold = settings.STT_CONFIDENCE_THRESHOLD

        if result.is_empty:
            raise LowConfidenceTranscriptionError(
                message=(
                    "Voice message samajh nahi aya. "
                    "Kripya message dobara text mein bhejein."
                ),
                confidence=result.confidence,
                transcript="",
            )

        if not result.meets_confidence(threshold):
            raise LowConfidenceTranscriptionError(
                message=(
                    "Voice message bilkul clear nahi tha. "
                    "Kripya amount ya details dobara text mein bhejein."
                ),
                confidence=result.confidence,
                transcript=result.text,
            )

    # ------------------------------------------------------------------
    # Main entry point: full audio → transcript pipeline
    # ------------------------------------------------------------------

    async def process_audio_message(
        self,
        media_id: str,
        access_token: str,
        mime_type: Optional[str] = None,
        language_hint: Optional[str] = None,
        check_confidence: bool = True,
    ) -> TranscriptionResult:
        """End-to-end pipeline: download → size check → transcribe → confidence check.

        Args:
            media_id:         Meta media object ID from the webhook.
            access_token:     WhatsApp Cloud API Bearer token.
            mime_type:        Optional MIME type override (normally resolved
                              from Meta metadata during download).
            language_hint:    BCP-47 language hint forwarded to the STT API.
            check_confidence: When True (default), raises
                              LowConfidenceTranscriptionError if the transcript
                              doesn't meet the threshold.  Set False only when
                              you want the raw result regardless of quality
                              (e.g. for logging / debugging).

        Returns:
            TranscriptionResult with a reliable, non-empty transcript.

        Raises:
            SpeechTranscriptionError:      on download or API failure.
            LowConfidenceTranscriptionError: when transcript is unclear
                                            (only if check_confidence=True).
        """
        # Download
        audio_bytes, resolved_mime = await self.download_audio(media_id, access_token)

        # Use caller-supplied mime_type override if provided
        effective_mime = mime_type or resolved_mime

        # Size guard
        self._check_size(audio_bytes, media_id)

        # Transcribe
        result = await self.transcribe(
            audio_bytes=audio_bytes,
            mime_type=effective_mime,
            language_hint=language_hint,
        )

        logger.info(
            "Transcription result.",
            extra={
                "media_id": media_id,
                "provider": result.provider,
                "confidence": result.confidence,
                "language": result.language,
                "text_length": len(result.text),
                "meets_threshold": result.meets_confidence(settings.STT_CONFIDENCE_THRESHOLD),
            },
        )

        # Confidence gate
        if check_confidence:
            self._check_confidence(result)

        return result

    # ------------------------------------------------------------------
    # Convenience: transcribe from already-downloaded bytes
    # ------------------------------------------------------------------

    async def transcribe_with_confidence_check(
        self,
        audio_bytes: bytes,
        mime_type: str = "audio/ogg",
        language_hint: Optional[str] = None,
    ) -> TranscriptionResult:
        """Transcribe pre-downloaded bytes and apply the confidence gate.

        Useful when tests or other callers already hold the audio bytes and
        don't need the download step.
        """
        result = await self.transcribe(
            audio_bytes=audio_bytes,
            mime_type=mime_type,
            language_hint=language_hint,
        )
        self._check_confidence(result)
        return result
