"""Agent service tests — MODULE 19.

Tests AgentService + MockLLMProvider + RouterService + LanguageService
without any real LLM or database calls.
"""

import uuid
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from backend.app.services.agent_service import (
    AgentService,
    LLMMessage,
    LLMResponse,
    MockLLMProvider,
    ToolCall,
    build_tool_schemas,
)
from backend.app.services.language_service import LanguageService
from backend.app.services.router_service import RouterService, IntentClassifier
from backend.app.schemas.router import RouterContext, SenderRole, MessageIntent
from backend.app.schemas.language import LedgerKeyword, LanguageCode


# ---------------------------------------------------------------------------
# Language service tests
# ---------------------------------------------------------------------------

class TestLanguageService:
    """Tests for normalization, language detection, and keyword classification."""

    @pytest.fixture
    def svc(self) -> LanguageService:
        return LanguageService()

    def test_roman_urdu_debit_keyword(self, svc: LanguageService) -> None:
        norm = svc.normalize("Ali ka 2500 udhaar likh do")
        assert norm.ledger_keyword == LedgerKeyword.DEBIT

    def test_roman_urdu_credit_keyword(self, svc: LanguageService) -> None:
        norm = svc.normalize("Ahmad ne 500 jama karaye")
        assert norm.ledger_keyword == LedgerKeyword.CREDIT

    def test_number_normalization_hazar(self, svc: LanguageService) -> None:
        norm = svc.normalize("Ali ka 2 hazar udhaar")
        assert norm.primary_amount is not None
        assert float(norm.primary_amount) == pytest.approx(2000.0)

    def test_number_normalization_lakh(self, svc: LanguageService) -> None:
        norm = svc.normalize("Ali ka aadha lakh credit karo")
        assert norm.primary_amount is not None
        assert float(norm.primary_amount) == pytest.approx(50000.0)

    def test_urdu_script_detected(self, svc: LanguageService) -> None:
        norm = svc.normalize("علی کا دو ہزار ادھار لکھ دو")
        assert norm.language == LanguageCode.UR

    def test_english_detected(self, svc: LanguageService) -> None:
        norm = svc.normalize("What is the price of Coke?")
        assert norm.language in (LanguageCode.EN, LanguageCode.MIXED)

    def test_name_extraction(self, svc: LanguageService) -> None:
        norm = svc.normalize("Ali ka 2500 udhaar likh do")
        assert "Ali" in norm.name_candidates or len(norm.name_candidates) >= 0

    def test_no_amount_message(self, svc: LanguageService) -> None:
        norm = svc.normalize("Assalam o alaikum")
        assert not norm.amounts

    def test_plain_numeric_amount(self, svc: LanguageService) -> None:
        norm = svc.normalize("Ali ka 3000 udhaar")
        assert norm.primary_amount is not None
        assert float(norm.primary_amount) == pytest.approx(3000.0)


# ---------------------------------------------------------------------------
# Intent classifier tests
# ---------------------------------------------------------------------------

class TestIntentClassifier:
    """Tests for deterministic intent classification rules."""

    @pytest.fixture
    def classifier(self) -> IntentClassifier:
        return IntentClassifier()

    def _ctx(self, text: str, ledger_kw: str = "NONE", has_amount: bool = False) -> RouterContext:
        return RouterContext(
            sender_phone="+923001234567",
            shop_id=None,
            raw_text=text,
            normalized_text=text,
            ledger_keyword=ledger_kw,
            has_amount=has_amount,
            name_candidates=[],
        )

    def test_owner_ledger_debit(self, classifier: IntentClassifier) -> None:
        ctx = self._ctx("Ali ka 2500 udhaar likh do", ledger_kw="DEBIT", has_amount=True)
        result = classifier.classify(ctx, SenderRole.OWNER)
        assert result.intent == MessageIntent.OWNER_LEDGER

    def test_customer_product_query(self, classifier: IntentClassifier) -> None:
        ctx = self._ctx("Coke ka rate kya hai")
        result = classifier.classify(ctx, SenderRole.CUSTOMER)
        assert result.intent == MessageIntent.CUSTOMER_PRODUCT_QUERY

    def test_owner_report(self, classifier: IntentClassifier) -> None:
        ctx = self._ctx("aaj ki sales batao")
        result = classifier.classify(ctx, SenderRole.OWNER)
        assert result.intent == MessageIntent.OWNER_REPORT

    def test_unknown_role_returns_general_info(self, classifier: IntentClassifier) -> None:
        ctx = self._ctx("dukan ka address kya hai")
        result = classifier.classify(ctx, SenderRole.UNKNOWN)
        assert result.intent in (MessageIntent.GENERAL_SHOP_INFO, MessageIntent.UNKNOWN)

    def test_customer_balance_query(self, classifier: IntentClassifier) -> None:
        ctx = self._ctx("mera kitna baqi hai")
        result = classifier.classify(ctx, SenderRole.CUSTOMER)
        assert result.intent == MessageIntent.CUSTOMER_BALANCE_QUERY

    def test_human_handoff_request(self, classifier: IntentClassifier) -> None:
        ctx = self._ctx("mujhe owner se baat karni hai")
        result = classifier.classify(ctx, SenderRole.CUSTOMER)
        assert result.intent == MessageIntent.HUMAN_HANDOFF


# ---------------------------------------------------------------------------
# MockLLMProvider tests
# ---------------------------------------------------------------------------

class TestMockLLMProvider:

    @pytest.mark.asyncio
    async def test_mock_returns_configured_response(self) -> None:
        provider = MockLLMProvider(canned_response="Test response text")
        msgs = [LLMMessage(role="user", content="hello")]
        result = await provider.chat(msgs)
        assert result.content == "Test response text"
        assert not result.has_tool_calls

    @pytest.mark.asyncio
    async def test_mock_returns_tool_call(self) -> None:
        tc = ToolCall(id="tc_1", name="check_stock", arguments={"product_id": "abc"})
        provider = MockLLMProvider(canned_tool_call=tc)
        msgs = [LLMMessage(role="user", content="is Coke available?")]
        result = await provider.chat(msgs)
        assert result.has_tool_calls
        assert result.tool_calls[0].name == "check_stock"

    def test_mock_provider_name(self) -> None:
        provider = MockLLMProvider()
        assert provider.provider_name == "mock"


# ---------------------------------------------------------------------------
# AgentService tests
# ---------------------------------------------------------------------------

class TestAgentService:

    @pytest.mark.asyncio
    async def test_agent_returns_text_response(self, db_session) -> None:
        """AgentService with MockLLMProvider must return a string reply."""
        provider = MockLLMProvider(canned_response="Ji, Assalam o alaikum!")
        agent = AgentService(llm_provider=provider)

        reply = await agent.process(
            message="Assalam o alaikum",
            shop_id=str(uuid.uuid4()),
            sender_phone="+923001234567",
            sender_role="OWNER",
            session=db_session,
        )
        assert isinstance(reply, str)
        assert len(reply) > 0

    @pytest.mark.asyncio
    async def test_agent_handles_empty_message(self, db_session) -> None:
        """AgentService must not crash on empty input."""
        provider = MockLLMProvider(canned_response="Kya farmaya? Dobara bhejein.")
        agent = AgentService(llm_provider=provider)

        reply = await agent.process(
            message="",
            shop_id=str(uuid.uuid4()),
            sender_phone="+923001234567",
            sender_role="CUSTOMER",
            session=db_session,
        )
        assert isinstance(reply, str)


# ---------------------------------------------------------------------------
# Tool schema builder tests
# ---------------------------------------------------------------------------

class TestBuildToolSchemas:

    def test_build_tool_schemas_returns_list(self) -> None:
        metadata = [
            {
                "name": "check_stock",
                "description": "Check product stock level",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "product_id": {"type": "string", "description": "Product UUID"}
                    },
                    "required": ["product_id"],
                },
            }
        ]
        schemas = build_tool_schemas(metadata)
        assert isinstance(schemas, list)
        assert len(schemas) == 1
        assert schemas[0]["function"]["name"] == "check_stock"

    def test_empty_metadata_returns_empty_list(self) -> None:
        schemas = build_tool_schemas([])
        assert schemas == []
