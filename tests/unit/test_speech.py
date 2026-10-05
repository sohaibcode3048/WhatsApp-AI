"""MODULE 07 — Voice Processing: complete test suite.

Coverage:
  TranscriptionResult
    - is_empty: blank/whitespace/non-empty
    - meets_confidence: at/above/below threshold

  SpeechToTextProvider (ABC)
    - cannot be instantiated directly
    - concrete subclass satisfies the contract

  MockSpeechProvider
    - returns configured text and confidence
    - honours should_raise

  _derive_confidence (GroqWhisperProvider internal)
    - empty segments → 0.0
    - single segment with logprob
    - high no_speech_prob lowers score
    - multiple segments averaged

  GroqWhisperProvider
    - no API key → mock result with confidence 0.0
    - 200 with verbose_json segments → correct confidence derived
    - 200 with no segments → fixed 0.80
    - 200 with empty transcript → confidence 0.0
    - 401 → SpeechTranscriptionError (auth)
    - 400 → SpeechTranscriptionError (bad audio)
    - 500 → SpeechTranscriptionError (server error)
    - network error → SpeechTranscriptionError
    - non-JSON response → SpeechTranscriptionError
    - language_hint forwarded when supported
    - unknown mime_type uses default filename

  SpeechService._check_size
    - below limit → passes
    - exactly at limit → passes
    - above limit → SpeechTranscriptionError

  SpeechService._check_confidence
    - empty transcript → LowConfidenceTranscriptionError
    - below threshold → LowConfidenceTranscriptionError (with partial text)
    - at threshold → passes
    - above threshold → passes

  SpeechService.download_audio
    - success → (bytes, mime_type)
    - metadata 401 → SpeechTranscriptionError
    - metadata non-200 → SpeechTranscriptionError
    - metadata missing url → SpeechTranscriptionError
    - CDN non-200 → SpeechTranscriptionError
    - network error on metadata → SpeechTranscriptionError
    - network error on CDN → SpeechTranscriptionError

  SpeechService.transcribe_with_confidence_check
    - high confidence → returns result
    - low confidence → LowConfidenceTranscriptionError

  SpeechService.process_audio_message
    - success end-to-end
    - low confidence raises LowConfidenceTranscriptionError
    - download failure raises SpeechTranscriptionError
    - file too large raises SpeechTranscriptionError

  WhatsAppService + SpeechService integration
    - audio message: successful transcription stored in normalized_message
    - audio message: low confidence → normalized_message stays as [AUDIO_ID:...]
    - audio message: STT error → normalized_message stays as [AUDIO_ID:...]
    - audio message: no ACCESS_TOKEN → STT skipped, raw token stored
    - text message: STT never called
"""

import json
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import settings
from backend.app.core.exceptions import (
    LowConfidenceTranscriptionError,
    SpeechTranscriptionError,
)
from backend.app.services.providers.groq_whisper import (
    GroqWhisperProvider,
    _derive_confidence,
)
from backend.app.services.providers.stt_base import (
    SpeechToTextProvider,
    TranscriptionResult,
)
from backend.app.services.speech_service import MockSpeechProvider, SpeechService
from backend.app.services.whatsapp_service import WhatsAppService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FAKE_AUDIO = b"\x00\x01\x02\x03" * 256   # 1 KB of fake audio bytes

_GROQ_VERBOSE_RESPONSE = {
    "task": "transcribe",
    "language": "ur",
    "duration": 4.5,
    "text": "Ali ka do hazar udhar likh do",
    "segments": [
        {
            "id": 0,
            "avg_logprob": -0.15,
            "no_speech_prob": 0.02,
            "text": "Ali ka do hazar",
        },
        {
            "id": 1,
            "avg_logprob": -0.20,
            "no_speech_prob": 0.03,
            "text": " udhar likh do",
        },
    ],
    "x_groq": {"id": "req_abc123"},
}


def _mock_httpx_response(status_code: int, body: dict | str) -> MagicMock:
    """Build a minimal httpx.Response-like mock."""
    resp = MagicMock()
    resp.status_code = status_code
    if isinstance(body, dict):
        resp.json.return_value = body
        resp.text = json.dumps(body)
    else:
        resp.text = body
        resp.json.side_effect = Exception("not JSON")
    resp.content = b""
    return resp


def _audio_webhook_payload(
    msg_id: str = "wamid.AUDIO01",
    from_phone: str = "923007654321",
    audio_media_id: str = "media_abc123",
    mime_type: str = "audio/ogg",
    phone_number_id: str = "100200300400",
) -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "entry-1",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "+923001234567",
                                "phone_number_id": phone_number_id,
                            },
                            "messages": [
                                {
                                    "from": from_phone,
                                    "id": msg_id,
                                    "timestamp": "1720000000",
                                    "type": "audio",
                                    "audio": {"id": audio_media_id, "mime_type": mime_type},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }


# ===========================================================================
# TranscriptionResult
# ===========================================================================


class TestTranscriptionResult:
    def test_is_empty_blank(self):
        r = TranscriptionResult(text="", confidence=0.9)
        assert r.is_empty is True

    def test_is_empty_whitespace(self):
        r = TranscriptionResult(text="   \t\n", confidence=0.9)
        assert r.is_empty is True

    def test_is_empty_false(self):
        r = TranscriptionResult(text="Hello", confidence=0.9)
        assert r.is_empty is False

    def test_meets_confidence_at_threshold(self):
        r = TranscriptionResult(text="Hi", confidence=0.6)
        assert r.meets_confidence(0.6) is True

    def test_meets_confidence_above(self):
        r = TranscriptionResult(text="Hi", confidence=0.95)
        assert r.meets_confidence(0.6) is True

    def test_meets_confidence_below(self):
        r = TranscriptionResult(text="Hi", confidence=0.4)
        assert r.meets_confidence(0.6) is False

    def test_defaults(self):
        r = TranscriptionResult(text="test", confidence=0.8)
        assert r.language is None
        assert r.duration_secs is None
        assert r.provider == "unknown"
        assert r.raw_response == {}


# ===========================================================================
# SpeechToTextProvider (ABC)
# ===========================================================================


class TestSpeechToTextProviderABC:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            SpeechToTextProvider()  # type: ignore[abstract]

    def test_concrete_subclass_works(self):
        class DummyProvider(SpeechToTextProvider):
            @property
            def provider_name(self) -> str:
                return "dummy"

            async def transcribe(self, audio_bytes, mime_type="audio/ogg", language_hint=None):
                return TranscriptionResult(text="ok", confidence=1.0, provider="dummy")

        p = DummyProvider()
        assert p.provider_name == "dummy"


# ===========================================================================
# MockSpeechProvider
# ===========================================================================


class TestMockSpeechProvider:
    async def test_returns_configured_text_and_confidence(self):
        p = MockSpeechProvider()
        p.mock_text = "Ali ka 2500 udhar"
        p.mock_confidence = 0.92
        result = await p.transcribe(FAKE_AUDIO)
        assert result.text == "Ali ka 2500 udhar"
        assert result.confidence == 0.92
        assert result.provider == "mock"

    async def test_honours_should_raise(self):
        p = MockSpeechProvider()
        p.should_raise = SpeechTranscriptionError("forced error")
        with pytest.raises(SpeechTranscriptionError, match="forced error"):
            await p.transcribe(FAKE_AUDIO)

    async def test_default_values(self):
        p = MockSpeechProvider()
        result = await p.transcribe(FAKE_AUDIO)
        assert "[MOCK_TRANSCRIPT]" in result.text
        assert result.confidence == 0.90
        assert result.language == "ur"
        assert result.duration_secs == 3.0


# ===========================================================================
# _derive_confidence
# ===========================================================================


class TestDeriveConfidence:
    def test_empty_segments_returns_zero(self):
        assert _derive_confidence([]) == 0.0

    def test_single_zero_logprob_near_one(self):
        # avg_logprob=0 → exp(0)=1, no_speech_prob=0 → 1.0
        score = _derive_confidence([{"avg_logprob": 0.0, "no_speech_prob": 0.0}])
        assert score > 0.99

    def test_logprob_minus_half(self):
        # exp(-0.5) ≈ 0.607
        import math
        score = _derive_confidence([{"avg_logprob": -0.5, "no_speech_prob": 0.0}])
        assert abs(score - math.exp(-0.5)) < 0.01

    def test_high_no_speech_prob_lowers_score(self):
        # avg_logprob=0 but no_speech_prob=0.9 → confidence ~0.1
        score = _derive_confidence([{"avg_logprob": 0.0, "no_speech_prob": 0.9}])
        assert score < 0.15

    def test_multiple_segments_averaged(self):
        segments = [
            {"avg_logprob": -0.1, "no_speech_prob": 0.01},
            {"avg_logprob": -0.2, "no_speech_prob": 0.01},
        ]
        score = _derive_confidence(segments)
        assert 0.7 < score < 1.0

    def test_missing_logprob_uses_fallback(self):
        # Segment without avg_logprob should use the conservative 0.75 base
        score = _derive_confidence([{"no_speech_prob": 0.0}])
        assert score == pytest.approx(0.75, abs=0.01)

    def test_very_low_logprob_clamped(self):
        # avg_logprob=-100 clamped to -3 → exp(-3) ≈ 0.05
        import math
        score = _derive_confidence([{"avg_logprob": -100.0, "no_speech_prob": 0.0}])
        assert score == pytest.approx(math.exp(-3.0), abs=0.01)


# ===========================================================================
# GroqWhisperProvider
# ===========================================================================


class TestGroqWhisperProvider:
    async def test_no_api_key_returns_mock_result(self, monkeypatch):
        monkeypatch.setattr(settings, "GROQ_API_KEY", None)
        p = GroqWhisperProvider()
        result = await p.transcribe(FAKE_AUDIO)
        assert "MOCK" in result.text
        assert result.confidence == 0.0
        assert result.provider == "groq_whisper"

    async def test_success_with_verbose_json_segments(self, monkeypatch):
        monkeypatch.setattr(settings, "GROQ_API_KEY", "test_key")
        p = GroqWhisperProvider()
        mock_resp = _mock_httpx_response(200, _GROQ_VERBOSE_RESPONSE)

        with patch("httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_resp
            )
            result = await p.transcribe(FAKE_AUDIO)

        assert result.text == "Ali ka do hazar udhar likh do"
        assert result.language == "ur"
        assert result.duration_secs == pytest.approx(4.5)
        assert result.provider == "groq_whisper"
        # Confidence should be derived from segments, not zero
        assert result.confidence > 0.0

    async def test_success_no_segments_gives_fixed_confidence(self, monkeypatch):
        monkeypatch.setattr(settings, "GROQ_API_KEY", "test_key")
        p = GroqWhisperProvider()
        body = {
            "text": "Hello world",
            "language": "en",
            "duration": 2.0,
            # no "segments" key
        }
        mock_resp = _mock_httpx_response(200, body)

        with patch("httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_resp
            )
            result = await p.transcribe(FAKE_AUDIO)

        assert result.text == "Hello world"
        assert result.confidence == pytest.approx(0.80)

    async def test_empty_transcript_gives_zero_confidence(self, monkeypatch):
        monkeypatch.setattr(settings, "GROQ_API_KEY", "test_key")
        p = GroqWhisperProvider()
        body = {"text": "   ", "language": "en", "duration": 1.0, "segments": []}
        mock_resp = _mock_httpx_response(200, body)

        with patch("httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_resp
            )
            result = await p.transcribe(FAKE_AUDIO)

        assert result.text == ""   # stripped
        assert result.confidence == 0.0

    async def test_401_raises_speech_error(self, monkeypatch):
        monkeypatch.setattr(settings, "GROQ_API_KEY", "bad_key")
        p = GroqWhisperProvider()
        mock_resp = _mock_httpx_response(401, {"error": "unauthorized"})

        with patch("httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_resp
            )
            with pytest.raises(SpeechTranscriptionError, match="authentication"):
                await p.transcribe(FAKE_AUDIO)

    async def test_400_raises_speech_error(self, monkeypatch):
        monkeypatch.setattr(settings, "GROQ_API_KEY", "test_key")
        p = GroqWhisperProvider()
        mock_resp = _mock_httpx_response(400, {"error": "invalid audio"})

        with patch("httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_resp
            )
            with pytest.raises(SpeechTranscriptionError, match="rejected"):
                await p.transcribe(FAKE_AUDIO)

    async def test_500_raises_speech_error(self, monkeypatch):
        monkeypatch.setattr(settings, "GROQ_API_KEY", "test_key")
        p = GroqWhisperProvider()
        mock_resp = _mock_httpx_response(500, "Internal Server Error")
        mock_resp.json.side_effect = Exception("not JSON")

        with patch("httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_resp
            )
            with pytest.raises(SpeechTranscriptionError, match="server error"):
                await p.transcribe(FAKE_AUDIO)

    async def test_network_error_raises_speech_error(self, monkeypatch):
        monkeypatch.setattr(settings, "GROQ_API_KEY", "test_key")
        p = GroqWhisperProvider()

        with patch("httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__.return_value.post = AsyncMock(
                side_effect=httpx.ConnectError("connection refused")
            )
            with pytest.raises(SpeechTranscriptionError, match="Network error"):
                await p.transcribe(FAKE_AUDIO)

    async def test_non_json_response_raises_speech_error(self, monkeypatch):
        monkeypatch.setattr(settings, "GROQ_API_KEY", "test_key")
        p = GroqWhisperProvider()
        mock_resp = _mock_httpx_response(200, "not json at all")

        with patch("httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_resp
            )
            with pytest.raises(SpeechTranscriptionError, match="non-JSON"):
                await p.transcribe(FAKE_AUDIO)

    async def test_language_hint_included_when_supported(self, monkeypatch):
        monkeypatch.setattr(settings, "GROQ_API_KEY", "test_key")
        p = GroqWhisperProvider()
        mock_resp = _mock_httpx_response(200, {"text": "ok", "language": "ur"})
        captured_data: list = []

        async def mock_post(url, headers, files, data):
            captured_data.append(data)
            return mock_resp

        with patch("httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__.return_value.post = AsyncMock(
                side_effect=mock_post
            )
            await p.transcribe(FAKE_AUDIO, language_hint="ur")

        assert captured_data[0].get("language") == "ur"

    async def test_unsupported_language_hint_omitted(self, monkeypatch):
        monkeypatch.setattr(settings, "GROQ_API_KEY", "test_key")
        p = GroqWhisperProvider()
        mock_resp = _mock_httpx_response(200, {"text": "ok"})
        captured_data: list = []

        async def mock_post(url, headers, files, data):
            captured_data.append(data)
            return mock_resp

        with patch("httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__.return_value.post = AsyncMock(
                side_effect=mock_post
            )
            # "xx" is not in _SUPPORTED_LANGUAGE_HINTS
            await p.transcribe(FAKE_AUDIO, language_hint="xx")

        assert "language" not in captured_data[0]


# ===========================================================================
# SpeechService._check_size
# ===========================================================================


class TestSpeechServiceCheckSize:
    def _service(self) -> SpeechService:
        return SpeechService(provider=MockSpeechProvider())

    def test_below_limit_passes(self):
        svc = self._service()
        svc._check_size(b"x" * 100, "mid")   # no exception

    def test_at_limit_passes(self):
        svc = self._service()
        limit = svc._MAX_BYTES
        svc._check_size(b"x" * limit, "mid")  # exact limit is fine

    def test_above_limit_raises(self):
        svc = self._service()
        over = b"x" * (svc._MAX_BYTES + 1)
        with pytest.raises(SpeechTranscriptionError, match="too large"):
            svc._check_size(over, "mid")


# ===========================================================================
# SpeechService._check_confidence
# ===========================================================================


class TestSpeechServiceCheckConfidence:
    def _service(self) -> SpeechService:
        return SpeechService(provider=MockSpeechProvider())

    def test_empty_transcript_raises(self):
        svc = self._service()
        r = TranscriptionResult(text="", confidence=0.95)
        with pytest.raises(LowConfidenceTranscriptionError) as exc_info:
            svc._check_confidence(r)
        assert exc_info.value.details["confidence"] == 0.95

    def test_whitespace_only_raises(self):
        svc = self._service()
        r = TranscriptionResult(text="   ", confidence=0.95)
        with pytest.raises(LowConfidenceTranscriptionError):
            svc._check_confidence(r)

    def test_below_threshold_raises_with_partial_text(self):
        svc = self._service()
        r = TranscriptionResult(text="unclear mumble", confidence=0.3)
        with pytest.raises(LowConfidenceTranscriptionError) as exc_info:
            svc._check_confidence(r)
        assert exc_info.value.details["partial_transcript"] == "unclear mumble"
        assert exc_info.value.details["confidence"] == pytest.approx(0.3)

    def test_at_threshold_passes(self):
        svc = self._service()
        threshold = settings.STT_CONFIDENCE_THRESHOLD
        r = TranscriptionResult(text="Ali ka 2000", confidence=threshold)
        svc._check_confidence(r)   # must not raise

    def test_above_threshold_passes(self):
        svc = self._service()
        r = TranscriptionResult(text="Ali ka 2000", confidence=0.99)
        svc._check_confidence(r)   # must not raise


# ===========================================================================
# SpeechService.download_audio (httpx mocked)
# ===========================================================================


class TestSpeechServiceDownloadAudio:
    def _service(self) -> SpeechService:
        return SpeechService(provider=MockSpeechProvider())

    def _make_meta_resp(self, url: str = "https://cdn.facebook.com/audio.ogg",
                         mime: str = "audio/ogg") -> MagicMock:
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"url": url, "mime_type": mime}
        resp.text = ""
        return resp

    def _make_cdn_resp(self, content: bytes = FAKE_AUDIO) -> MagicMock:
        resp = MagicMock()
        resp.status_code = 200
        resp.content = content
        return resp

    async def test_success_returns_bytes_and_mime(self):
        svc = self._service()
        meta_resp = self._make_meta_resp()
        cdn_resp = self._make_cdn_resp()

        with patch("httpx.AsyncClient") as MockClient:
            mock_get = AsyncMock(side_effect=[meta_resp, cdn_resp])
            MockClient.return_value.__aenter__.return_value.get = mock_get

            audio, mime = await svc.download_audio("media_123", "token_abc")

        assert audio == FAKE_AUDIO
        assert mime == "audio/ogg"

    async def test_metadata_401_raises(self):
        svc = self._service()
        meta_resp = MagicMock()
        meta_resp.status_code = 401
        meta_resp.json.return_value = {}

        with patch("httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=meta_resp
            )
            with pytest.raises(SpeechTranscriptionError, match="auth failed"):
                await svc.download_audio("media_123", "bad_token")

    async def test_metadata_non_200_raises(self):
        svc = self._service()
        meta_resp = MagicMock()
        meta_resp.status_code = 404
        meta_resp.json.return_value = {}

        with patch("httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=meta_resp
            )
            with pytest.raises(SpeechTranscriptionError, match="metadata request failed"):
                await svc.download_audio("media_123", "token")

    async def test_metadata_missing_url_raises(self):
        svc = self._service()
        meta_resp = MagicMock()
        meta_resp.status_code = 200
        meta_resp.json.return_value = {"mime_type": "audio/ogg"}  # no "url"
        meta_resp.text = ""

        with patch("httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=meta_resp
            )
            with pytest.raises(SpeechTranscriptionError, match="missing 'url'"):
                await svc.download_audio("media_123", "token")

    async def test_cdn_non_200_raises(self):
        svc = self._service()
        meta_resp = self._make_meta_resp()
        cdn_resp = MagicMock()
        cdn_resp.status_code = 403

        with patch("httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__.return_value.get = AsyncMock(
                side_effect=[meta_resp, cdn_resp]
            )
            with pytest.raises(SpeechTranscriptionError, match="CDN download failed"):
                await svc.download_audio("media_123", "token")

    async def test_network_error_on_metadata_raises(self):
        svc = self._service()

        with patch("httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__.return_value.get = AsyncMock(
                side_effect=httpx.ConnectError("connection refused")
            )
            with pytest.raises(SpeechTranscriptionError, match="Network error"):
                await svc.download_audio("media_123", "token")

    async def test_network_error_on_cdn_raises(self):
        svc = self._service()
        meta_resp = self._make_meta_resp()

        with patch("httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__.return_value.get = AsyncMock(
                side_effect=[meta_resp, httpx.ConnectError("cdn timeout")]
            )
            with pytest.raises(SpeechTranscriptionError, match="Network error"):
                await svc.download_audio("media_123", "token")


# ===========================================================================
# SpeechService.transcribe_with_confidence_check
# ===========================================================================


class TestSpeechServiceTranscribeWithConfidenceCheck:
    async def test_high_confidence_returns_result(self):
        provider = MockSpeechProvider()
        provider.mock_text = "Ali ka 2500 udhar"
        provider.mock_confidence = 0.95
        svc = SpeechService(provider=provider)
        result = await svc.transcribe_with_confidence_check(FAKE_AUDIO)
        assert result.text == "Ali ka 2500 udhar"
        assert result.confidence == 0.95

    async def test_low_confidence_raises(self):
        provider = MockSpeechProvider()
        provider.mock_text = "unclear"
        provider.mock_confidence = 0.1
        svc = SpeechService(provider=provider)
        with pytest.raises(LowConfidenceTranscriptionError):
            await svc.transcribe_with_confidence_check(FAKE_AUDIO)

    async def test_empty_text_raises(self):
        provider = MockSpeechProvider()
        provider.mock_text = ""
        provider.mock_confidence = 0.99
        svc = SpeechService(provider=provider)
        with pytest.raises(LowConfidenceTranscriptionError):
            await svc.transcribe_with_confidence_check(FAKE_AUDIO)


# ===========================================================================
# SpeechService.process_audio_message
# ===========================================================================


class TestSpeechServiceProcessAudioMessage:
    def _service_with_mock_download(
        self,
        audio: bytes = FAKE_AUDIO,
        mime: str = "audio/ogg",
        transcript: str = "Ali ka 2500 udhar",
        confidence: float = 0.92,
    ) -> SpeechService:
        provider = MockSpeechProvider()
        provider.mock_text = transcript
        provider.mock_confidence = confidence
        svc = SpeechService(provider=provider)
        # Patch download_audio to avoid real HTTP
        svc.download_audio = AsyncMock(return_value=(audio, mime))
        return svc

    async def test_success_end_to_end(self):
        svc = self._service_with_mock_download()
        result = await svc.process_audio_message(
            media_id="media_001",
            access_token="token_abc",
        )
        assert result.text == "Ali ka 2500 udhar"
        assert result.confidence == pytest.approx(0.92)
        assert result.provider == "mock"

    async def test_low_confidence_raises(self):
        svc = self._service_with_mock_download(transcript="mumble", confidence=0.2)
        with pytest.raises(LowConfidenceTranscriptionError) as exc_info:
            await svc.process_audio_message(
                media_id="media_002",
                access_token="token_abc",
            )
        assert exc_info.value.details["confidence"] == pytest.approx(0.2)

    async def test_check_confidence_false_returns_low_result(self):
        """With check_confidence=False even a low-confidence result is returned."""
        svc = self._service_with_mock_download(transcript="mumble", confidence=0.1)
        result = await svc.process_audio_message(
            media_id="media_003",
            access_token="token_abc",
            check_confidence=False,
        )
        assert result.text == "mumble"
        assert result.confidence == pytest.approx(0.1)

    async def test_download_failure_raises(self):
        provider = MockSpeechProvider()
        svc = SpeechService(provider=provider)
        svc.download_audio = AsyncMock(
            side_effect=SpeechTranscriptionError("download failed")
        )
        with pytest.raises(SpeechTranscriptionError, match="download failed"):
            await svc.process_audio_message("media_004", "token_abc")

    async def test_file_too_large_raises(self):
        provider = MockSpeechProvider()
        svc = SpeechService(provider=provider)
        huge_audio = b"x" * (svc._MAX_BYTES + 1)
        svc.download_audio = AsyncMock(return_value=(huge_audio, "audio/ogg"))
        with pytest.raises(SpeechTranscriptionError, match="too large"):
            await svc.process_audio_message("media_005", "token_abc")

    async def test_mime_type_override_used(self):
        """Explicit mime_type kwarg takes precedence over resolved mime from download."""
        provider = MockSpeechProvider()
        provider.mock_text = "ok"
        provider.mock_confidence = 0.95
        svc = SpeechService(provider=provider)
        svc.download_audio = AsyncMock(return_value=(FAKE_AUDIO, "audio/ogg"))

        # Capture what mime_type reaches the provider
        captured: list = []
        original_transcribe = provider.transcribe

        async def spy_transcribe(audio_bytes, mime_type="audio/ogg", language_hint=None):
            captured.append(mime_type)
            return await original_transcribe(audio_bytes, mime_type, language_hint)

        provider.transcribe = spy_transcribe

        await svc.process_audio_message(
            "media_006", "token_abc", mime_type="audio/mp4"
        )
        assert captured[0] == "audio/mp4"


# ===========================================================================
# WhatsAppService + SpeechService integration (via async_client fixture)
# ===========================================================================


class TestWhatsAppServiceSTTIntegration:
    async def test_audio_message_transcription_stored_in_normalized(
        self, async_client, db_session: AsyncSession
    ):
        """High-confidence audio → normalized_message = transcript text."""
        from sqlalchemy import select
        from backend.app.models.message import Message
        from backend.app.services.speech_service import MockSpeechProvider, SpeechService

        provider = MockSpeechProvider()
        provider.mock_text = "Ali ka teen hazar udhar likh do"
        provider.mock_confidence = 0.95

        speech_svc = SpeechService(provider=provider)
        # Patch download to avoid real HTTP
        speech_svc.download_audio = AsyncMock(return_value=(FAKE_AUDIO, "audio/ogg"))

        # Inject the speech service via WhatsAppService constructor
        with patch(
            "backend.app.services.whatsapp_service.SpeechService",
            return_value=speech_svc,
        ):
            # Set a fake ACCESS_TOKEN so the STT branch is triggered
            with patch.object(settings, "WHATSAPP_ACCESS_TOKEN", "fake_token"):
                payload = _audio_webhook_payload(msg_id="wamid.STT_OK")
                resp = await async_client.post(
                    "/webhook/whatsapp",
                    content=json.dumps(payload),
                    headers={"Content-Type": "application/json"},
                )

        assert resp.status_code == 200
        assert resp.json()["processed"] == 1

        result = await db_session.execute(
            select(Message).where(Message.whatsapp_message_id == "wamid.STT_OK")
        )
        msg = result.scalar_one()
        assert msg.message_type == "audio"
        # raw_message keeps the [AUDIO_ID:…] token
        assert "[AUDIO_ID:" in msg.raw_message
        # normalized_message is a JSON NormalizedMessage (LanguageService ran on transcript)
        import json as _json
        norm = _json.loads(msg.normalized_message)
        assert norm["raw_text"] == "Ali ka teen hazar udhar likh do"
        assert norm["ledger_keyword"] == "DEBIT"

    async def test_audio_message_low_confidence_keeps_token(
        self, async_client, db_session: AsyncSession
    ):
        """Low-confidence transcription → normalized_message = raw [AUDIO_ID:…] token."""
        from sqlalchemy import select
        from backend.app.models.message import Message
        from backend.app.services.speech_service import MockSpeechProvider, SpeechService

        provider = MockSpeechProvider()
        provider.mock_text = "unclear noise"
        provider.mock_confidence = 0.1  # below STT_CONFIDENCE_THRESHOLD

        speech_svc = SpeechService(provider=provider)
        speech_svc.download_audio = AsyncMock(return_value=(FAKE_AUDIO, "audio/ogg"))

        with patch(
            "backend.app.services.whatsapp_service.SpeechService",
            return_value=speech_svc,
        ):
            with patch.object(settings, "WHATSAPP_ACCESS_TOKEN", "fake_token"):
                payload = _audio_webhook_payload(msg_id="wamid.STT_LOW")
                resp = await async_client.post(
                    "/webhook/whatsapp",
                    content=json.dumps(payload),
                    headers={"Content-Type": "application/json"},
                )

        assert resp.status_code == 200

        result = await db_session.execute(
            select(Message).where(Message.whatsapp_message_id == "wamid.STT_LOW")
        )
        msg = result.scalar_one()
        assert "[AUDIO_ID:" in msg.normalized_message   # token not replaced

    async def test_audio_message_stt_error_keeps_token(
        self, async_client, db_session: AsyncSession
    ):
        """STT download failure → message still stored, normalized = raw token."""
        from sqlalchemy import select
        from backend.app.models.message import Message
        from backend.app.services.speech_service import MockSpeechProvider, SpeechService

        provider = MockSpeechProvider()
        speech_svc = SpeechService(provider=provider)
        speech_svc.download_audio = AsyncMock(
            side_effect=SpeechTranscriptionError("CDN unreachable")
        )

        with patch(
            "backend.app.services.whatsapp_service.SpeechService",
            return_value=speech_svc,
        ):
            with patch.object(settings, "WHATSAPP_ACCESS_TOKEN", "fake_token"):
                payload = _audio_webhook_payload(msg_id="wamid.STT_ERR")
                resp = await async_client.post(
                    "/webhook/whatsapp",
                    content=json.dumps(payload),
                    headers={"Content-Type": "application/json"},
                )

        assert resp.status_code == 200  # webhook must always ack

        result = await db_session.execute(
            select(Message).where(Message.whatsapp_message_id == "wamid.STT_ERR")
        )
        msg = result.scalar_one()
        assert "[AUDIO_ID:" in msg.normalized_message

    async def test_audio_message_no_access_token_skips_stt(
        self, async_client, db_session: AsyncSession
    ):
        """When WHATSAPP_ACCESS_TOKEN is not set the STT branch is skipped entirely."""
        from sqlalchemy import select
        from backend.app.models.message import Message

        with patch.object(settings, "WHATSAPP_ACCESS_TOKEN", None):
            payload = _audio_webhook_payload(msg_id="wamid.NO_TOKEN")
            resp = await async_client.post(
                "/webhook/whatsapp",
                content=json.dumps(payload),
                headers={"Content-Type": "application/json"},
            )

        assert resp.status_code == 200

        result = await db_session.execute(
            select(Message).where(Message.whatsapp_message_id == "wamid.NO_TOKEN")
        )
        msg = result.scalar_one()
        # No transcription ran — normalized_message is the raw token
        assert "[AUDIO_ID:" in msg.normalized_message

    async def test_text_message_stt_never_called(
        self, async_client, db_session: AsyncSession
    ):
        """Text messages must never touch the STT pipeline."""
        from sqlalchemy import select
        from backend.app.models.message import Message

        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "entry-1",
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {
                                    "display_phone_number": "+923001234567",
                                    "phone_number_id": "100200300400",
                                },
                                "messages": [
                                    {
                                        "from": "923007654321",
                                        "id": "wamid.TEXT_NOSTT",
                                        "timestamp": "1720000000",
                                        "type": "text",
                                        "text": {"body": "Hello"},
                                    }
                                ],
                            },
                        }
                    ],
                }
            ],
        }

        with patch(
            "backend.app.services.whatsapp_service.SpeechService"
        ) as MockSpeechService:
            resp = await async_client.post(
                "/webhook/whatsapp",
                content=json.dumps(payload),
                headers={"Content-Type": "application/json"},
            )
            # SpeechService should not be instantiated for text messages
            MockSpeechService.assert_not_called()

        assert resp.status_code == 200

        result = await db_session.execute(
            select(Message).where(Message.whatsapp_message_id == "wamid.TEXT_NOSTT")
        )
        msg = result.scalar_one()
        # normalized_message is a JSON NormalizedMessage — check normalized_text field
        import json as _json
        norm = _json.loads(msg.normalized_message)
        assert norm["normalized_text"] == "Hello"
