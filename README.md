# WhatsApp AI Munshi

A production-grade, multi-tenant AI business assistant for small shops and retail businesses in Pakistan and South Asia. It operates entirely over WhatsApp, communicates in Urdu, Roman Urdu, and English (text + voice notes), and manages orders, inventory, ledger, payments, reminders, and reports — all without giving the AI direct database access.

---

## Table of Contents

1. [How the System Works](#how-the-system-works)
2. [Architecture Overview](#architecture-overview)
3. [Technology Stack](#technology-stack)
4. [Project Structure](#project-structure)
5. [Key Design Decisions](#key-design-decisions)
6. [Quick Start (Local)](#quick-start-local)
7. [Docker Deployment](#docker-deployment)
8. [Environment Variables](#environment-variables)
9. [Running Tests](#running-tests)
10. [All Test Cases](#all-test-cases)
11. [API Reference](#api-reference)
12. [Background Jobs](#background-jobs)
13. [Troubleshooting](#troubleshooting)

---

## How the System Works

Every inbound WhatsApp message flows through a fixed 10-step pipeline:

```
Customer / Owner WhatsApp Message
        │
        ▼
1.  POST /webhook/whatsapp
        │  Meta HMAC signature verified
        │  Duplicate message rejected (idempotency by whatsapp_message_id)
        │
        ▼
2.  Message Stored (messages table — raw_message preserved forever)
        │
        ▼
3.  Voice-to-Text (Groq Whisper) — if audio message
        │  Low-confidence transcript → ask user to re-send as text
        │
        ▼
4.  Language Normalization
        │  Urdu number words → decimals  (hazar→1000, lakh→100000)
        │  Keyword mapping  (udhar→DEBIT, jama→CREDIT)
        │
        ▼
5.  Role Resolution + Intent Routing
        │  phone_number → users table → customers table → UNKNOWN
        │  Deterministic rules classify intent (11 categories)
        │
        ▼
6.  RAG Retrieval (ChromaDB) — for shop knowledge queries
        │  Shop policies, FAQs, delivery rules (per-shop isolated)
        │  Transactional queries (balance, stock) bypass RAG → PostgreSQL
        │
        ▼
7.  AI Agent (Groq LLM)
        │  System prompt + role context + RAG chunks + conversation history
        │  LLM emits structured tool-call requests (never raw SQL)
        │
        ▼
8.  10-Stage Safety Harness (for every tool call)
        │  1. Pydantic schema validation
        │  2. Authentication (sender identity)
        │  3. Authorization (RBAC by role)
        │  4. Tenant isolation (shop_id verified on every entity)
        │  5. Business rule validation (positive amounts, valid IDs)
        │  6. Confirmation check  (financial ops ask "Haan/Nahi")
        │  7. Approval check      (large amounts > Rs.10,000 → owner)
        │  8. Idempotency check   (SHA-256 dedup key)
        │  9. Atomic DB transaction (ledger + audit in one commit)
        │  10. Audit log written
        │
        ▼
9.  Localized Response (Roman Urdu / Urdu / English per shop config)
        │
        ▼
10. WhatsApp Reply sent via Meta Cloud API
```

### Financial Safety Guarantee

The LLM **never** touches the database directly. It can only call named tools. Every tool call passes all 10 harness stages before any data is written. A ledger entry and its audit log are committed atomically — if either fails, the whole transaction rolls back. Balances are never stored as a single overwritable field; they are always calculated as `SUM(DEBIT) - SUM(CREDIT)` from immutable append-only ledger rows.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────┐
│                    WhatsApp Cloud API                │
│          (Meta webhook — inbound + outbound)         │
└────────────────────┬────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────┐
│                  FastAPI Backend                      │
│                                                       │
│  ┌──────────┐  ┌───────────┐  ┌──────────────────┐  │
│  │ Webhook  │  │ Dashboard │  │ /metrics endpoint │  │
│  │ Handler  │  │  REST API │  │  (observability)  │  │
│  └────┬─────┘  └───────────┘  └──────────────────┘  │
│       │                                               │
│  ┌────▼──────────────────────────────────┐           │
│  │           Pipeline Service            │           │
│  │  Language → Router → Agent → Reply    │           │
│  └────┬──────────────────────────────────┘           │
│       │                                               │
│  ┌────▼──────────────────────────────────┐           │
│  │         AI Agent (Groq LLM)           │           │
│  │   GroqProvider / MockLLMProvider      │           │
│  └────┬──────────────────────────────────┘           │
│       │  tool calls                                   │
│  ┌────▼──────────────────────────────────┐           │
│  │       10-Stage Safety Harness         │           │
│  └────┬──────────────────────────────────┘           │
│       │                                               │
│  ┌────▼────────┐  ┌───────────┐  ┌───────────────┐  │
│  │ PostgreSQL  │  │  Redis    │  │  ChromaDB     │  │
│  │ (main data) │  │(cache/    │  │  (RAG vector  │  │
│  │             │  │ Celery)   │  │   store)      │  │
│  └─────────────┘  └───────────┘  └───────────────┘  │
└─────────────────────────────────────────────────────┘
         │
┌────────▼──────────────────────────────────────────┐
│              Celery Workers                        │
│  daily_summary  |  payment_reminders  |  stock_alerts │
└───────────────────────────────────────────────────┘
```

---

## Technology Stack

| Layer | Technology |
|---|---|
| Backend framework | FastAPI 0.115+ |
| Data validation | Pydantic v2 |
| Database ORM | SQLAlchemy 2.x async |
| Migrations | Alembic |
| Primary database | PostgreSQL 16 (SQLite for dev/test) |
| Cache / queue broker | Redis 7 |
| Background jobs | Celery 5 |
| AI / LLM | Groq API (llama-3.3-70b-versatile) |
| Voice-to-Text | Groq Whisper (whisper-large-v3) |
| Embeddings | sentence-transformers/all-MiniLM-L6-v2 |
| Vector store | ChromaDB 0.5 |
| Authentication | JWT (python-jose) + bcrypt |
| WhatsApp | Meta Cloud API v21.0 |
| Containerization | Docker + Docker Compose |
| Testing | pytest + pytest-asyncio |

---

## Project Structure

```
My project/
├── backend/
│   └── app/
│       ├── main.py                    # FastAPI app factory
│       ├── core/
│       │   ├── config.py              # All settings (pydantic-settings)
│       │   ├── database.py            # Async SQLAlchemy engine + session
│       │   ├── security.py            # JWT, bcrypt, HMAC, idempotency hash
│       │   ├── exceptions.py          # Full domain exception hierarchy
│       │   ├── logging.py             # Structured JSON logging
│       │   ├── observability.py       # In-process metrics store (MODULE 24)
│       │   └── security_middleware.py # Rate limiter + injection detector (MODULE 25)
│       ├── api/routes/
│       │   ├── whatsapp.py            # POST/GET /webhook/whatsapp
│       │   ├── health.py              # GET /health, GET /ping
│       │   ├── metrics.py             # GET /metrics (MODULE 24)
│       │   ├── auth.py                # POST /auth/login, GET /auth/me
│       │   ├── shops.py               # GET/PATCH /shops/{id}
│       │   ├── customers.py           # CRUD /customers
│       │   ├── products.py            # CRUD /products
│       │   ├── orders.py              # CRUD /orders
│       │   ├── ledger.py              # /ledger entries + balance
│       │   ├── approvals.py           # /approvals approve/reject
│       │   ├── audit_logs.py          # GET /audit-logs
│       │   └── reports.py             # GET /reports/daily
│       ├── models/                    # SQLAlchemy ORM models
│       ├── schemas/                   # Pydantic request/response schemas
│       ├── repositories/              # Database access layer (no ORM in routes)
│       ├── services/
│       │   ├── pipeline_service.py    # Full message pipeline orchestrator
│       │   ├── agent_service.py       # LLMProvider + AgentService (MODULE 19)
│       │   ├── whatsapp_service.py    # Meta API client + message ingestion
│       │   ├── router_service.py      # Role resolution + intent classification
│       │   ├── language_service.py    # Normalization + language detection
│       │   ├── speech_service.py      # STT pipeline (MODULE 07)
│       │   └── audit_service.py       # Audit log writer
│       ├── tools/                     # Agent-callable tools (MODULEs 10–14)
│       │   ├── base.py                # BaseTool + 10-stage harness + ToolRegistry
│       │   ├── stock_tools.py         # check_stock, update_stock, get_low_stock
│       │   ├── customer_tools.py      # find_customer, get_customer
│       │   ├── ledger_tools.py        # add_ledger_entry, get_balance, record_payment
│       │   ├── order_tools.py         # create_order, get_order, cancel_order
│       │   ├── reporting_tools.py     # daily_summary, get_sales_summary
│       │   ├── management_tools.py    # add_customer, add_product, update_product
│       │   └── reminder_tools.py      # send_reminder
│       ├── harness/                   # Safety harness components
│       │   ├── permissions.py         # RBAC permission sets
│       │   ├── validation.py          # Domain-level validators
│       │   ├── confirmation.py        # Redis-backed Haan/Nahi confirmation engine
│       │   ├── approval.py            # DB-persisted approval engine for large ops
│       │   ├── idempotency.py         # Redis-backed dedup by SHA-256 key
│       │   └── fallback.py            # Human handoff handler
│       ├── rag/                       # RAG knowledge system (MODULE 18)
│       │   ├── embeddings.py          # HuggingFace sentence-transformers
│       │   ├── vector_store.py        # ChromaDB + mock fallback
│       │   ├── ingestion.py           # Chunk → embed → store
│       │   └── retriever.py           # Semantic search + score filtering
│       ├── prompts/
│       │   ├── system_prompt.py       # Core agent system prompt
│       │   ├── customer_prompt.py     # Customer role instructions
│       │   └── owner_prompt.py        # Owner/staff role instructions
│       └── workers/                   # Celery background tasks (MODULE 21)
│           ├── celery_app.py          # Celery factory + beat schedule
│           ├── tasks.py               # ping, ingest_rag_document, send_whatsapp
│           ├── reminders.py           # Daily payment reminder sweep
│           ├── summaries.py           # Nightly sales summary
│           └── stock_alerts.py        # Noon low-stock alert sweep
├── alembic/                           # Database migrations
│   └── versions/                      # 7 migration files (includes password_hash)
├── tests/
│   ├── conftest.py                    # Shared fixtures (in-memory SQLite, async client)
│   ├── unit/                          # Unit tests for all modules (13 files)
│   ├── integration/                   # End-to-end HTTP tests (MODULE 09+20)
│   ├── agent/                         # Agent + router + language tests (MODULE 19)
│   ├── tools/                         # Tool harness tests (MODULEs 10–14)
│   ├── security/                      # Security hardening tests (MODULE 25)
│   └── evaluation/                    # 100+ labelled dataset + runner (MODULE 23)
├── scripts/
│   ├── seed_data.py                   # Demo shop + customers + products
│   ├── run_migrations.py              # Programmatic Alembic runner
│   └── health_check.py               # Docker health check helper
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
├── alembic.ini
└── pytest.ini
```

---

## Key Design Decisions

### The LLM is Untrusted

The AI never generates SQL, never accesses the database directly, and never bypasses confirmation. It only emits structured JSON tool-call requests. Every request passes through all 10 harness stages before touching data.

### Immutable Ledger

Balances are never stored as an overwritable field. Every financial event is an immutable row with type `DEBIT` or `CREDIT`. The current balance is always calculated from the ledger rows:

```
Balance = SUM(amount WHERE type=DEBIT) - SUM(amount WHERE type=CREDIT)
```

### RAG vs PostgreSQL Boundary

| Query type | Route |
|---|---|
| "Ali ka balance kitna hai?" | PostgreSQL (get_balance tool) |
| "10 Coke available hain?" | PostgreSQL (check_stock tool) |
| "Return policy kya hai?" | ChromaDB RAG |
| "Delivery charges?" | ChromaDB RAG |

The agent is instructed never to use RAG for transactional data.

### Multi-Tenancy

Every database table has a `shop_id` foreign key. Every repository method filters by `shop_id`. Every tool verifies that referenced entities (customer, product, order) belong to the active `shop_id`. The RAG vector store uses per-shop collections (`munshi_shop_{shop_id}`).

### Confirmation Flow

```
Owner: "Ali ka 2500 udhaar likh do"
Agent: "Ali ka Rs. 2,500 udhaar ledger mein add kar doon? Haan / Nahi"
Owner: "Haan"
Agent: [executes tool] "Done. Ali ke ledger mein Rs. 2,000 add kar diya."
```

Confirmation state is stored in Redis with a 10-minute TTL. If the owner changes the amount mid-confirmation ("nahi, 3000"), a fresh confirmation is started.

### Approval Flow (large amounts)

Operations above `LARGE_CREDIT_APPROVAL_THRESHOLD` (default Rs. 10,000) are not executed immediately. An `approvals` record is created and the owner is notified. Execution only happens after explicit approval via `POST /api/v1/approvals/{id}/approve`.

---

## Quick Start (Local)

### Prerequisites

- Python 3.12+
- Redis (for confirmation/idempotency/Celery)
- Optional: PostgreSQL 16 (SQLite used by default in dev)
- Optional: ChromaDB (in-memory mock used if unavailable)

### Steps

```powershell
# 1. Clone and enter project
cd "My project"

# 2. Create and activate virtual environment
python -m venv .venv
.venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Copy and configure environment
Copy-Item .env.example .env
# Edit .env — set at minimum: GROQ_API_KEY, WHATSAPP_ACCESS_TOKEN, etc.

# 5. Run database migrations
python scripts/run_migrations.py

# 6. Seed demo data (optional)
python scripts/seed_data.py

# 7. Start the development server
uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000

# 8. Start Celery worker (separate terminal)
celery -A backend.app.workers.celery_app worker --loglevel=info

# 9. Start Celery beat scheduler (separate terminal)
celery -A backend.app.workers.celery_app beat --loglevel=info
```

The API will be available at `http://localhost:8000`. In DEBUG mode, the interactive docs are at `http://localhost:8000/docs`.

---

## Docker Deployment

```powershell
# Build and start all services (backend, postgres, redis, chroma)
docker compose up --build

# Run in background
docker compose up -d

# Run migrations after first start
docker compose exec backend python scripts/run_migrations.py

# Seed demo data
docker compose exec backend python scripts/seed_data.py

# View logs
docker compose logs -f backend

# Stop all services
docker compose down
```

### Services started by Docker Compose

| Service | Port | Description |
|---|---|---|
| backend | 8000 | FastAPI application + Celery worker |
| postgres | 5432 | PostgreSQL 16 |
| redis | 6379 | Redis 7 (cache + Celery broker) |
| chroma | 8001 | ChromaDB vector store |

---

## Environment Variables

Copy `.env.example` to `.env` and fill in the values.

| Variable | Required | Description |
|---|---|---|
| `DATABASE_URL` | Yes | Async DB URL (`postgresql+asyncpg://...` or `sqlite+aiosqlite:///./munshi.db`) |
| `DATABASE_SYNC_URL` | Yes | Sync URL for Alembic (`postgresql+psycopg2://...`) |
| `REDIS_URL` | Yes | Redis connection URL |
| `CELERY_BROKER_URL` | No | Defaults to Redis DB 1 |
| `CELERY_RESULT_BACKEND` | No | Defaults to Redis DB 2 |
| `GROQ_API_KEY` | Yes* | Groq API key for LLM + Whisper STT |
| `GROQ_MODEL` | No | Default: `llama-3.3-70b-versatile` |
| `WHATSAPP_ACCESS_TOKEN` | Yes* | Meta WhatsApp Cloud API token |
| `WHATSAPP_PHONE_NUMBER_ID` | Yes* | Meta phone number ID |
| `WHATSAPP_VERIFY_TOKEN` | Yes | Webhook verification token |
| `WHATSAPP_APP_SECRET` | Yes* | For HMAC signature verification |
| `JWT_SECRET` | Yes | Min 32 chars, kept secret |
| `SECRET_KEY` | Yes | App secret key, min 32 chars |
| `EMBEDDING_MODEL` | No | Default: `sentence-transformers/all-MiniLM-L6-v2` |
| `CHROMA_HOST` | No | Default: `localhost` |
| `CHROMA_PORT` | No | Default: `8001` |
| `LARGE_CREDIT_APPROVAL_THRESHOLD` | No | Default: `10000.00` (PKR) |
| `APP_ENV` | No | `development` / `staging` / `production` / `test` |
| `DEBUG` | No | `true` enables `/docs` and open CORS |

*Required in production. In development the system falls back gracefully (mock LLM, simulated WhatsApp sends, mock STT).

---

## Running Tests

The test suite uses **pytest** with **pytest-asyncio**. All tests run against an in-memory SQLite database — no PostgreSQL, Redis, or external APIs required.

### Run all tests

```powershell
pytest
```

### Run specific test suites

```powershell
# Unit tests only (fastest)
pytest tests/unit/ -v

# Integration tests (HTTP end-to-end)
pytest tests/integration/ -v

# Agent + router + language tests
pytest tests/agent/ -v

# Tool harness tests
pytest tests/tools/ -v

# Security hardening tests
pytest tests/security/ -v

# Evaluation framework (intent/language accuracy)
pytest tests/evaluation/ -v

# All tests with coverage report
pytest --tb=short -q
```

### Run evaluation runner directly

```powershell
# Fast: router-only evaluation (no LLM, no DB)
python -m tests.evaluation.runner

# See full JSON report
cat evaluation_report.json
```

### Pytest configuration

`pytest.ini` is pre-configured with `asyncio_mode = auto` so all `async def test_*` functions run automatically without `@pytest.mark.asyncio` on every function.

---

## All Test Cases

### Unit Tests (`tests/unit/`)

| File | What it tests |
|---|---|
| `test_health.py` | GET /health database probe, GET /ping liveness |
| `test_config.py` | Settings loading, env overrides, defaults |
| `test_database.py` | Async engine creation, session factory, connection |
| `test_models.py` | ORM model creation, relationships, constraints |
| `test_repositories.py` | CRUD operations on all repositories |
| `test_ledger.py` | Immutable ledger, DEBIT/CREDIT, balance calculation, rollback |
| `test_orders.py` | Order creation, stock decrement, cancel + stock restore |
| `test_language.py` | Roman Urdu normalization, number words, keyword detection |
| `test_router.py` | Intent classification for all 11 intent categories |
| `test_speech.py` | STT provider interface, confidence gating, low-confidence rejection |
| `test_tools.py` | BaseTool harness, RBAC, schema validation, tenant isolation |
| `test_webhook.py` | Meta HMAC verification, webhook challenge, payload parsing |
| `test_audit.py` | AuditLog creation, sanitization of sensitive fields |

### Integration Tests (`tests/integration/`)

| Test | What it verifies |
|---|---|
| `test_webhook_verification_challenge` | META handshake returns the challenge string |
| `test_webhook_invalid_token_rejected` | Wrong verify token → 403 |
| `test_inbound_text_message_stored` | Inbound message persisted to messages table |
| `test_duplicate_message_not_processed_twice` | Same whatsapp_message_id deduplicated |
| `test_health_check` | GET /health returns ok + database component |
| `test_ping_endpoint` | GET /ping returns pong immediately |
| `test_metrics_endpoint` | GET /metrics returns full snapshot dict |
| `test_auth_login_invalid_credentials` | Wrong password → 401 |
| `test_customers_endpoint_requires_auth` | No JWT → 401 |
| `test_products_endpoint_requires_auth` | No JWT → 401 |
| `test_ledger_endpoint_requires_auth` | No JWT → 401 |
| `test_orders_endpoint_requires_auth` | No JWT → 401 |

### Agent Tests (`tests/agent/`)

| Test class | What it tests |
|---|---|
| `TestLanguageService` | Roman Urdu DEBIT/CREDIT detection, hazar/lakh normalization, Urdu script detection, English detection, name extraction |
| `TestIntentClassifier` | OWNER_LEDGER, CUSTOMER_PRODUCT_QUERY, OWNER_REPORT, GENERAL_SHOP_INFO, CUSTOMER_BALANCE_QUERY, HUMAN_HANDOFF |
| `TestMockLLMProvider` | Canned text response, canned tool call, provider name |
| `TestAgentService` | Text response return, empty message handling |
| `TestBuildToolSchemas` | OpenAI-compatible schema generation, empty list |

### Tools Tests (`tests/tools/`)

| Test class | What it tests |
|---|---|
| `TestToolRegistry` | All 18 tools registered, get() raises on unknown |
| `TestToolAuthorization` | CUSTOMER blocked from add_ledger_entry, UNKNOWN blocked from update_stock |
| `TestSchemaValidation` | Missing required field → ValidationError, negative amount rejected |
| `TestStockTools` | check_stock (found/not found), update_stock (increase/prevent negative), get_low_stock |
| `TestCustomerTools` | find_customer by name, get_customer by id, wrong shop → TenantIsolationError |
| `TestLedgerTools` | Zero balance for new customer, balance after DEBIT, DEBIT-CREDIT calculation |
| `TestManagementTools` | add_customer creates record, idempotency, STAFF cannot add_product, find_product |

### Security Tests (`tests/security/`)

| Test | What it verifies |
|---|---|
| `test_injection_detected` (×12) | All known injection patterns are flagged |
| `test_clean_messages_not_flagged` (×10) | Legitimate Roman Urdu messages pass through |
| `test_empty_message_not_flagged` | Empty string is safe |
| `test_oversized_message_not_flagged` | Messages > 4000 chars returned as non-injection |
| `test_rate_limiter_allows_in_test_env` | Rate limiter disabled during tests |
| `test_security_headers_present` | X-Content-Type-Options, X-Frame-Options, X-XSS-Protection on every response |
| `test_negative_amount_rejected_by_schema` | amount ≤ 0 → 422 (never reaches database) |
| `test_duplicate_webhook_idempotency` | Same message_id twice → second marked as duplicate |
| `test_health_endpoint_returns_ok` | Baseline sanity |
| `test_metrics_endpoint_returns_json` | /metrics structure intact |
| `test_sql_injection_in_query_param` | SQL in query param → never 500 |
| `test_sql_injection_in_path` | SQL in path param → 400/404/422 |

### Evaluation Tests (`tests/evaluation/`)

100+ labelled messages across 20 categories with automated threshold assertions:

| Metric | Threshold | What it measures |
|---|---|---|
| Intent accuracy | ≥ 70% | Correct category routing |
| Language accuracy | ≥ 80% | Correct language detection |
| Hallucination rate | ≤ 20% | Tool called when no tool was needed |
| Injection block rate | ≥ 50% | SEC-* cases blocked at routing layer |
| Zero evaluation errors | 100% | No uncaught exceptions in router/language service |

#### Evaluation dataset categories

| Category | Count | Examples |
|---|---|---|
| OWNER_LEDGER (Roman Urdu) | 10 | "Ali ka 2500 udhaar likh do", number words |
| Customer balance queries | 5 | "mera kitna baqi hai", "hisab kya hai" |
| Customer orders | 5 | "2 Coke chahiye", order status |
| Product queries | 5 | "Coke ka rate", availability |
| Stock management | 5 | Check/update/low-stock |
| Reports | 5 | "aaj ki sales", weekly summary |
| Customer management | 4 | add/find/get customer |
| Shop info (RAG) | 5 | Return policy, delivery, hours |
| Configuration | 3 | Price update, add product |
| Mixed language | 3 | Roman Urdu + English combined |
| Typos / noisy text | 4 | "hia" for "hai", "kok" for "Coke" |
| Ambiguous inputs | 6 | Single name, bare amount, two customers |
| Security / adversarial | 10 | Prompt injection, SQL, jailbreak, data theft |
| Idempotency / duplicates | 2 | Same message sent twice |
| Human handoff | 3 | "mujhe owner se baat karni hai" |
| Unknown / unsupported | 4 | Jokes, weather, emoji-only |
| Confirmation replies | 4 | "Haan", "Nahi", mid-confirmation change |
| Reminders | 2 | Send reminder to customer |
| Staff restriction | 2 | Staff trying owner-only operation |
| Cross-tenant | 2 | Accessing another shop's data |

---

## API Reference

### Public / Webhook

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Health check with DB probe |
| GET | `/ping` | Liveness probe (no DB) |
| GET | `/metrics` | Operational metrics snapshot |
| GET | `/webhook/whatsapp` | Meta webhook verification |
| POST | `/webhook/whatsapp` | Inbound message processing |

### Authentication

| Method | Path | Description |
|---|---|---|
| POST | `/api/v1/auth/login` | Phone + password → JWT |
| GET | `/api/v1/auth/me` | Current user info from JWT |

### Shop

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/api/v1/shops/{shop_id}` | Any role | Get shop details |
| PATCH | `/api/v1/shops/{shop_id}` | Owner | Update shop settings |

### Customers

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/api/v1/customers` | Staff+ | List customers (filter by name) |
| POST | `/api/v1/customers` | Staff+ | Create customer |
| GET | `/api/v1/customers/{id}` | Staff+ | Get customer details |
| PATCH | `/api/v1/customers/{id}` | Staff+ | Update customer |
| GET | `/api/v1/customers/{id}/balance` | Staff+ | Current ledger balance |

### Products

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/api/v1/products` | Any | List products (filter by name, low_stock) |
| POST | `/api/v1/products` | Owner | Add product |
| GET | `/api/v1/products/{id}` | Any | Get product details |
| PATCH | `/api/v1/products/{id}` | Owner | Update product |

### Orders

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/api/v1/orders` | Staff+ | List orders (filter by customer, status) |
| POST | `/api/v1/orders` | Staff+ | Create order (atomic stock deduction) |
| GET | `/api/v1/orders/{id}` | Staff+ | Get order with line items |
| POST | `/api/v1/orders/{id}/cancel` | Staff+ | Cancel order (restores stock) |

### Ledger

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/api/v1/ledger` | Staff+ | Customer ledger statement |
| GET | `/api/v1/ledger/balance` | Staff+ | Customer balance summary |
| POST | `/api/v1/ledger/entry` | Staff+ | Add manual ledger entry |
| POST | `/api/v1/ledger/payment` | Staff+ | Record payment received |

### Approvals

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/api/v1/approvals` | Owner | List pending approvals |
| GET | `/api/v1/approvals/{id}` | Owner | Get approval details |
| POST | `/api/v1/approvals/{id}/approve` | Owner | Approve the operation |
| POST | `/api/v1/approvals/{id}/reject` | Owner | Reject the operation |

### Reports & Audit

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/api/v1/reports/daily` | Staff+ | Daily sales summary |
| GET | `/api/v1/audit-logs` | Owner | Filtered audit trail |

---

## Background Jobs

Three scheduled Celery tasks run on a daily schedule:

| Job | Schedule | What it does |
|---|---|---|
| `send_daily_summary` | 23:55 daily | Calculates daily sales, orders, payments, top products, low stock. Sends summary to shop owner via WhatsApp. |
| `send_payment_reminders` | 10:00 daily | Scans all active customers with balance > 0. Sends polite Roman Urdu payment reminder via WhatsApp. Respects per-customer opt-out. |
| `send_low_stock_alerts` | 12:00 daily | Finds all products where `current_stock ≤ low_stock_threshold`. Sends alert to shop owner. |

One-off tasks (triggered by webhook processing or API calls):

| Task | Trigger | What it does |
|---|---|---|
| `ingest_rag_document` | POST /api/v1 or seed script | Chunks, embeds, and stores a document in ChromaDB off the main thread |
| `send_whatsapp_message` | Pipeline service | Fire-and-forget outbound WhatsApp send, with 2 retries |
| `ping` | Health check | Returns "pong" to verify Celery worker is alive |

---

## Troubleshooting

**Server starts but WhatsApp messages aren't processed**
- Check `WHATSAPP_VERIFY_TOKEN` matches what you set in the Meta Developer Console.
- In dev mode without `WHATSAPP_APP_SECRET`, HMAC verification is skipped. Set it in production.
- Ensure the webhook URL is publicly accessible (use ngrok for local testing: `ngrok http 8000`).

**"LLM not configured" response to every message**
- Set `GROQ_API_KEY` in your `.env` file.
- Restart the server after changing env vars.

**ChromaDB unavailable — RAG not working**
- The system falls back to a mock vector store automatically. Shop knowledge queries will return empty context.
- Start ChromaDB: `docker compose up chroma` or run `chroma run --host 0.0.0.0 --port 8001`.

**Celery tasks not running**
- Ensure Redis is running: `redis-cli ping` should return `PONG`.
- Check `CELERY_BROKER_URL` in `.env`.
- Start the worker: `celery -A backend.app.workers.celery_app worker --loglevel=info`.

**`AttributeError: 'Settings' object has no attribute 'CELERY_BROKER_URL'`**
- This was Bug 1, now fixed. Ensure you have the latest `backend/app/core/config.py`.

**Auth login always returns 401**
- Run `python scripts/seed_data.py` to create demo users with hashed passwords.
- The User model now has a `password_hash` column (Bug 4 fix). Run `python scripts/run_migrations.py` to apply migration `c9d2e4f6a830`.

**Test suite fails with import errors**
- Run from the project root: `pytest` (not from inside a subdirectory).
- Ensure `.venv` is activated and `requirements.txt` is installed.

**Rate limit returns 429 in tests**
- The rate limiter is disabled automatically when `APP_ENV=test`. Ensure `pytest.ini` or your `.env` sets `APP_ENV=test`.
