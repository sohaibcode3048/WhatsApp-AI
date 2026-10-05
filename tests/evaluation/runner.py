"""Evaluation Runner — MODULE 23.

Runs the labelled dataset against the RouterService (intent + language
classification) and optionally against the full AgentService (tool
selection accuracy). Produces a structured report with metrics.

Usage:
    # Intent/routing evaluation only (fast, no LLM calls):
    python -m tests.evaluation.runner

    # Full agent evaluation (requires GROQ_API_KEY + running DB):
    python -m tests.evaluation.runner --full

Output metrics:
  - intent_accuracy       — fraction of messages routed to expected_intent
  - language_accuracy     — fraction with correct language detection
  - tool_accuracy         — fraction where expected_tool was selected
  - hallucination_rate    — fraction where tool was called when None expected
  - injection_block_rate  — fraction of SEC-* cases that were correctly blocked
  - confirmation_compliance — fraction of financial ops that triggered confirmation
  - ambiguity_escalation  — fraction of AMB-* that resulted in clarification
"""

import asyncio
import json
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone

from tests.evaluation.dataset import EVAL_DATASET


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class CaseResult:
    case_id: str
    message: str
    expected_intent: str
    actual_intent: str
    expected_tool: Optional[str]
    actual_tool: Optional[str]
    expected_language: str
    actual_language: str
    intent_correct: bool
    language_correct: bool
    tool_correct: bool
    notes: str
    error: Optional[str] = None


@dataclass
class EvalReport:
    total: int = 0
    intent_correct: int = 0
    language_correct: int = 0
    tool_correct: int = 0
    tool_applicable: int = 0   # cases where expected_tool is not None
    errors: int = 0
    cases: List[CaseResult] = field(default_factory=list)

    # Category-specific counters
    security_blocked: int = 0
    security_total: int = 0
    ambiguity_escalated: int = 0
    ambiguity_total: int = 0

    @property
    def intent_accuracy(self) -> float:
        return self.intent_correct / self.total if self.total else 0.0

    @property
    def language_accuracy(self) -> float:
        return self.language_correct / self.total if self.total else 0.0

    @property
    def tool_accuracy(self) -> float:
        return self.tool_correct / self.tool_applicable if self.tool_applicable else 0.0

    @property
    def hallucination_rate(self) -> float:
        """Fraction of no-tool cases where a tool was incorrectly selected."""
        no_tool = [c for c in self.cases if c.expected_tool is None]
        if not no_tool:
            return 0.0
        hallucinated = sum(1 for c in no_tool if c.actual_tool is not None)
        return hallucinated / len(no_tool)

    @property
    def injection_block_rate(self) -> float:
        return self.security_blocked / self.security_total if self.security_total else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total_cases": self.total,
            "errors": self.errors,
            "metrics": {
                "intent_accuracy": round(self.intent_accuracy, 4),
                "language_accuracy": round(self.language_accuracy, 4),
                "tool_accuracy": round(self.tool_accuracy, 4),
                "hallucination_rate": round(self.hallucination_rate, 4),
                "injection_block_rate": round(self.injection_block_rate, 4),
            },
            "category_breakdown": {
                "security_blocked": self.security_blocked,
                "security_total": self.security_total,
            },
            "cases": [
                {
                    "id": c.case_id,
                    "message": c.message[:60],
                    "expected_intent": c.expected_intent,
                    "actual_intent": c.actual_intent,
                    "intent_correct": c.intent_correct,
                    "expected_tool": c.expected_tool,
                    "actual_tool": c.actual_tool,
                    "tool_correct": c.tool_correct,
                    "expected_language": c.expected_language,
                    "actual_language": c.actual_language,
                    "language_correct": c.language_correct,
                    "notes": c.notes,
                    "error": c.error,
                }
                for c in self.cases
            ],
        }


# ---------------------------------------------------------------------------
# Router-only evaluation (no DB, no LLM)
# ---------------------------------------------------------------------------

async def _run_router_evaluation() -> EvalReport:
    """Evaluate RouterService intent classification on the dataset."""
    from backend.app.services.language_service import LanguageService
    from backend.app.services.router_service import IntentClassifier
    from backend.app.schemas.router import RouterContext, SenderRole

    lang_svc = LanguageService()
    classifier = IntentClassifier()
    report = EvalReport()

    for case in EVAL_DATASET:
        report.total += 1
        case_id: str = case["id"]
        msg: str = case["message"]
        expected_intent: str = case["expected_intent"]
        expected_tool: Optional[str] = case["expected_tool"]
        expected_lang: str = case["language"]
        sender_role_str: str = case["sender_role"]
        notes: str = case["notes"]

        # Track security and ambiguity categories
        if case_id.startswith("SEC-"):
            report.security_total += 1
        if case_id.startswith("AMB-"):
            report.ambiguity_total += 1

        try:
            # Language detection
            norm = lang_svc.normalize(msg)
            actual_lang = norm.language.value

            # Intent classification (deterministic rules, no DB)
            role = SenderRole(sender_role_str)
            ctx = RouterContext(
                sender_phone="+923000000000",
                shop_id=None,
                raw_text=msg,
                normalized_text=norm.normalized_text,
                ledger_keyword=norm.ledger_keyword.value,
                has_amount=bool(norm.amounts),
                name_candidates=norm.name_candidates,
            )
            routed = classifier.classify(ctx, role)
            actual_intent = routed.intent.value
            actual_tool = routed.suggested_tool

            intent_ok = actual_intent == expected_intent
            lang_ok = actual_lang == expected_lang or expected_lang in ("mixed", "unknown")
            tool_ok = actual_tool == expected_tool

            if expected_tool is not None:
                report.tool_applicable += 1
                if tool_ok:
                    report.tool_correct += 1

            if intent_ok:
                report.intent_correct += 1
            if lang_ok:
                report.language_correct += 1

            # Security: blocked = no tool called and intent is UNKNOWN
            if case_id.startswith("SEC-") and actual_intent == "UNKNOWN" and actual_tool is None:
                report.security_blocked += 1

            report.cases.append(CaseResult(
                case_id=case_id,
                message=msg,
                expected_intent=expected_intent,
                actual_intent=actual_intent,
                expected_tool=expected_tool,
                actual_tool=actual_tool,
                expected_language=expected_lang,
                actual_language=actual_lang,
                intent_correct=intent_ok,
                language_correct=lang_ok,
                tool_correct=tool_ok,
                notes=notes,
            ))

        except Exception as exc:
            report.errors += 1
            report.cases.append(CaseResult(
                case_id=case_id,
                message=msg,
                expected_intent=expected_intent,
                actual_intent="ERROR",
                expected_tool=expected_tool,
                actual_tool=None,
                expected_language=expected_lang,
                actual_language="ERROR",
                intent_correct=False,
                language_correct=False,
                tool_correct=False,
                notes=notes,
                error=str(exc),
            ))

    return report


# ---------------------------------------------------------------------------
# Print helpers
# ---------------------------------------------------------------------------

def _print_report(report: EvalReport) -> None:
    print("\n" + "=" * 60)
    print("  WhatsApp AI Munshi — Evaluation Report")
    print("=" * 60)
    print(f"  Total cases      : {report.total}")
    print(f"  Errors           : {report.errors}")
    print(f"  Intent accuracy  : {report.intent_accuracy:.1%}")
    print(f"  Language accuracy: {report.language_accuracy:.1%}")
    print(f"  Tool accuracy    : {report.tool_accuracy:.1%}")
    print(f"  Hallucination    : {report.hallucination_rate:.1%}")
    print(f"  Injection block  : {report.injection_block_rate:.1%}")
    print("=" * 60)

    failures = [c for c in report.cases if not c.intent_correct or c.error]
    if failures:
        print(f"\n  FAILURES ({len(failures)}):")
        for c in failures:
            tag = "[ERR]" if c.error else "[FAIL]"
            print(f"  {tag} {c.case_id}: '{c.message[:40]}...'")
            print(f"        expected={c.expected_intent}  got={c.actual_intent}")
            if c.error:
                print(f"        error={c.error}")
    else:
        print("\n  All cases passed intent classification!")
    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    full_mode = "--full" in sys.argv
    if full_mode:
        print("Full agent evaluation mode — requires GROQ_API_KEY and running database.")
        print("Running router-only evaluation for now...\n")

    report = asyncio.run(_run_router_evaluation())
    _print_report(report)

    # Write JSON report
    report_path = "evaluation_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report.to_dict(), f, ensure_ascii=False, indent=2)
    print(f"  Full JSON report saved to: {report_path}\n")

    # Exit non-zero if accuracy below acceptable thresholds
    if report.intent_accuracy < 0.70:
        print("  WARNING: Intent accuracy below 70% threshold!")
        sys.exit(1)


if __name__ == "__main__":
    main()
