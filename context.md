# Project Context: WhatsApp AI Munshi (Production System)

## 1. Executive Summary
**WhatsApp AI Munshi** is a production-grade, multi-tenant digital accountant and store management assistant for retail shops and small businesses (SMEs) across Pakistan and South Asia. The system operates entirely over WhatsApp, interacting with shop owners, shop staff, and retail customers in their natural languages: **Urdu (Nastaliq script)**, **Roman Urdu**, and **English**, via both text and **voice notes**.

The core mission is financial accuracy, inventory reliability, and operational trust. **The AI (LLM) is treated as an untrusted reasoning agent** with zero direct access to the database. All operations are strictly guarded by a **10-stage AI Agent Safety Harness**.

---

## 2. Business Domain & Actors

### 2.1 User Roles & Permissions
| Role | Capabilities | Restrictions |
|---|---|---|
| **OWNER** | Full tenant access: view reports, modify stock, adjust ledger, approve high-credit requests, configure reminder schedules, manage staff. | Bound by confirmation prompts for financial mutations and strict idempotency checks. |
| **STAFF** | Record orders, check stock levels, view customer basic details, record cash payments. | Cannot delete records, adjust baseline product prices, or grant credit above configured thresholds without Owner approval. |
| **CUSTOMER** | View own order status, inquire product pricing & availability, check own ledger balance (if permitted by shop), receive payment reminders. | Strictly cannot access other customers' records or any internal shop ledger/costs. |
| **UNKNOWN** | Basic greeting, shop address, operating hours, public product catalog inquiries. | No access to financial, ledger, or order data. |

### 2.2 Multi-Tenancy Architecture
- Every business belongs to a unique tenant identified by `shop_id` (UUID).
- All database queries, vector store collections/metadata filters, cache keys, and audit logs are scoped by `shop_id`.
- Cross-tenant data leakage is prevented at the repository layer, tool harness layer, and RAG retrieval layer.

---

## 3. The 10-Stage AI Agent Safety Harness

The LLM never generates direct SQL, ORM queries, or arbitrary code. It interacts purely by emitting structured tool call requests (JSON). Every tool call must pass through the **Harness**:

```
[ LLM Emits Tool Call Request ]
              │
              ▼
    1. Schema Validation (Pydantic v2 strict typing)
              │
              ▼
    2. Authentication & Authorization (Verify sender phone -> role -> RBAC permissions)
              │
              ▼
    3. Multi-Tenant Check (Enforce explicit shop_id matching on all entities)
              │
              ▼
    4. Business Rule Validation (Stock availability, positive amounts, valid customer state)
              │
              ▼
    5. Confirmation Check (Financial or stock mutations require user "Haan/Nahi" confirmation)
              │
              ▼
    6. Threshold Approval Check (Transactions > threshold e.g. Rs 10,000 require Owner approval)
              │
              ▼
    7. Idempotency Check (Deduplicate by whatsapp_message_id and deterministic transaction hash)
              │
              ▼
    8. Atomic Database Transaction (Commit ledger entry + audit log in single atomic transaction)
              │
              ▼
    9. Audit Log Generation (Append-only record of user, tool, parameters, and before/after delta)
              │
              ▼
    10. Safe Localized Response (Format response in shop's configured language)
```

---

## 4. Ledger Architecture & Financial Guarantees

1. **Immutable Double-Entry Style Records**: Balances are never arbitrarily overwritten. Every financial event is an immutable entry with `type` (`DEBIT` or `CREDIT`), `amount`, `reference_type` (`ORDER`, `PAYMENT`, `MANUAL_ADJUSTMENT`), and `created_by`.
2. **Debit vs Credit Convention**:
   - `DEBIT`: Money owed by the customer to the shop (e.g., goods taken on credit/udhaar). Increases customer due balance.
   - `CREDIT`: Money paid by the customer to the shop (e.g., cash received, bank transfer). Decreases customer due balance.
   - **Balance Calculation**: `Current Balance = SUM(DEBIT) - SUM(CREDIT)`.
3. **Transactional Integrity**: Ledger writes and audit log writes occur within the same database transaction. If an error occurs, the entire transaction is rolled back.

---

## 5. Multi-Language & NLP Normalization

Small business owners communicate colloquially:
- **Urdu Script**: "علی کا دو ہزار ادھار لکھ دو"
- **Roman Urdu**: "Ali ka 2 hazar udhar likh do" / "Ali k 2000 likhdo khate me"
- **English**: "Add 2000 credit for Ali"

### Linguistic Rules:
- Number words in Urdu/Roman Urdu (`hazar` = 1,000, `sau` = 100, `lakh` = 100,000, `aadha` = 0.5) must normalize into standard decimals.
- Keywords for credit/udhaar (`udhar`, `baqi`, `khata`, `likh do`, `credit`) map to ledger `DEBIT` (money receivable).
- Keywords for payment (`jama`, `wasool`, `ada`, `mil gaye`, `paid`) map to ledger `CREDIT` (payment received).
- Both `raw_message` and `normalized_message` are retained for debugging, auditing, and re-processing.

---

## 6. RAG vs Transactional Data Boundary

| Query Type | Route | Data Source |
|---|---|---|
| **Transactional Data** (Dynamic, Real-time) | PostgreSQL Tools | "Ali ka balance kitna hai?", "10 Coke bottles available hain?", "Daily sales kitni hui?" |
| **Knowledge Data** (Static / Semi-static) | ChromaDB RAG | "Return policy kya hai?", "Delivery charges kitnay hain?", "Dukan kis time khulti hai?" |

*Rule: The agent must never use RAG to answer balance or stock queries.*

---

## 7. Database Entity Relationships

- `shops` (1) ───< `users` (N)
- `shops` (1) ───< `customers` (N)
- `shops` (1) ───< `products` (N)
- `shops` (1) ───< `orders` (N) ───< `order_items` (N)
- `customers` (1) ───< `ledger` (N)
- `shops` (1) ───< `audit_log` (N)
- `shops` (1) ───< `messages` (N)
- `shops` (1) ───< `approvals` (N)

---

## 8. 26-Module Implementation Status

- [x] **MODULE 01 — Project Foundation** *(Completed)*
- [x] **MODULE 02 — PostgreSQL & Core Models** *(Completed)*
- [x] **MODULE 03 — Orders & Inventory** *(Completed)*
- [x] **MODULE 04 — Ledger Engine** *(Completed)*
- [x] **MODULE 05 — Audit System** *(Completed)*
- [ ] **MODULE 06 — WhatsApp Webhook & Ingress** *(Next)*
- [ ] **MODULE 07 — Voice Processing (STT Provider)**
- [ ] **MODULE 08 — Language Normalization**
- [ ] **MODULE 09 — Customer/Owner Message Router**
- [ ] **MODULE 10 — Tool Framework**
- [ ] **MODULE 11 — Stock Tools**
- [ ] **MODULE 12 — Customer Tools**
- [ ] **MODULE 13 — Ledger Tools**
- [ ] **MODULE 14 — Order Tools**
- [ ] **MODULE 15 — Confirmation Engine**
- [ ] **MODULE 16 — Approval Engine**
- [ ] **MODULE 17 — Idempotency Engine**
- [ ] **MODULE 18 — RAG Knowledge Engine (ChromaDB)**
- [ ] **MODULE 19 — Agent Core & Groq Provider**
- [ ] **MODULE 20 — Agent + Harness Integration**
- [ ] **MODULE 21 — Background Jobs (Redis / Celery)**
- [ ] **MODULE 22 — Human Handoff**
- [ ] **MODULE 23 — Evaluation Framework (100+ Test Suite)**
- [ ] **MODULE 24 — Observability & Metrics**
- [ ] **MODULE 25 — Security Hardening**
- [ ] **MODULE 26 — Production Deployment & Final End-to-End Verification**
