<p align="center">
  <img src="frontend/public/storeflow-logo.svg" alt="StoreFlow - Order Support &amp; Shopping Agent" width="480" />
</p>

<p align="center">
  <strong>Production-Grade AI Order Support &amp; Intelligent Shopping Platform</strong><br>
  Built with FastAPI, Google Gemini, PostgreSQL, and React.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10+-3776AB?style=flat&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/FastAPI-0.115+-009688?style=flat&logo=fastapi&logoColor=white" alt="FastAPI">
  <img src="https://img.shields.io/badge/Google_Gemini-google--genai-4285F4?style=flat&logo=google&logoColor=white" alt="Google Gemini">
  <img src="https://img.shields.io/badge/PostgreSQL-14+-4169E1?style=flat&logo=postgresql&logoColor=white" alt="PostgreSQL">
  <img src="https://img.shields.io/badge/React-18.3-61DAFB?style=flat&logo=react&logoColor=black" alt="React">
  <img src="https://img.shields.io/badge/Vite-6.0-646CFF?style=flat&logo=vite&logoColor=white" alt="Vite">
  <img src="https://img.shields.io/badge/TypeScript-5.6-3178C6?style=flat&logo=typescript&logoColor=white" alt="TypeScript">
  <img src="https://img.shields.io/badge/Tailwind_CSS-3.4-38B2AC?style=flat&logo=tailwind-css&logoColor=white" alt="Tailwind CSS">
</p>

---

## Table of Contents

1. [System Overview](#system-overview)
2. [Key Architecture & Features](#key-architecture--features)
   - [Gemini AI Supervisor & Routing](#1-gemini-ai-supervisor--intent-routing)
   - [Deterministic Order Support Engine](#2-deterministic-order-support-engine)
   - [Secure Guest Verification & Reliable Outbox](#3-secure-guest-verification--reliable-outbox-worker)
   - [Customer Authentication & Authorization](#4-customer-authentication--authorization)
   - [Production Hardening (CORS, Rate Limiting, Headers)](#5-production-hardening-phase-1)
   - [StoreFlow Customer Frontend (SPA)](#6-storeflow-customer-frontend-spa)
3. [Technology Stack](#technology-stack)
4. [Database Migrations & Schema](#database-migrations--schema)
5. [API Specification](#api-specification)
6. [Local Development & Setup](#local-development--setup)
7. [Testing & Quality Assurance](#testing--quality-assurance)
8. [Security & Isolation Guarantees](#security--isolation-guarantees)

---

## System Overview

**StoreFlow** is an enterprise-grade ecommerce customer service platform that combines the reasoning capabilities of **Google Gemini** with deterministic database tools to answer customer questions, track shipments, inspect orders, and deliver intelligent shopping recommendations without hallucinations or cross-customer data exposure.

The platform provides a dual-interface architecture:
- **FastAPI Core Service**: High-performance asynchronous backend providing authenticated customer APIs, guest order OTP verification, an intelligent Gemini supervisor route, and distributed security controls.
- **StoreFlow React Frontend**: A responsive Single Page Application (SPA) built with React 18, Vite 6, TypeScript, and Tailwind CSS, adhering to StoreFlow's official design language (Navy `#0F172A`, Emerald `#10B981`, Slate `#64748B`).

```
                              ┌──────────────────────────────────────────────┐
                              │            StoreFlow React SPA               │
                              │ (Chat, Order Inspector, Guest Portal, Auth)  │
                              └──────────────────────┬───────────────────────┘
                                                     │ HTTPS / JSON / Bearer
                                                     ▼
                              ┌──────────────────────────────────────────────┐
                              │           FastAPI Gateway Service            │
                              │  - SecurityHeadersMiddleware (HSTS/CSP)      │
                              │  - Strict Origin CORSMiddleware              │
                              │  - Distributed Auth Rate Limiter             │
                              └───────┬──────────────────────────────┬───────┘
                                      │                              │
                     ┌────────────────┴─────────────┐                │
                     ▼                              ▼                ▼
     ┌──────────────────────────────┐ ┌───────────────────────────┐ ┌───────────────────────────┐
     │  Google Gemini Supervisor    │ │ Customer & Order Services │ │ Secure Guest Verification │
     │  - Intent Classification     │ │ - Argon2id Auth & JWT     │ │ - Cryptographic 6-Digit   │
     │  - Order Support Determinism │ │ - Customer Order History  │ │   SHA-256 OTP             │
     │  - Shopping Recommendations  │ │ - Order Inspector Toolset │ │ - Reliable Email Outbox   │
     │  - General Policies & FAQ    │ │ - Public Product Catalog  │ │   Asynchronous Worker     │
     └──────────────┬───────────────┘ └─────────────┬─────────────┘ └─────────────┬─────────────┘
                    │                               │                             │
                    └───────────────────────────────┼─────────────────────────────┘
                                                    ▼
                                    ┌───────────────────────────────┐
                                    │    PostgreSQL Database 14+    │
                                    │  - auth_rate_limits (0007)    │
                                    │  - guest_email_outbox (0005)  │
                                    │  - guest_verifications (0004) │
                                    │  - orders & items (0002)      │
                                    │  - products & inventory (0001)│
                                    └───────────────────────────────┘
```

---

## Key Architecture & Features

### 1. Gemini AI Supervisor & Intent Routing
Powered exclusively by Google's official `google-genai` Python SDK, the supervisor agent analyzes customer inquiries and routes them with deterministic safety:
- **`order_support`**: Scoped exclusively to authenticated customer accounts or verified guest tokens. Executes read-only database tools. Never hallucinates shipment dates, tracking codes, or monetary figures.
- **`shopping_agent`**: Understands natural language product queries, queries active product inventory, and responds with product recommendation reasons, pricing, and stock badges.
- **`general_assistant`**: Answers store policies, shipping timelines, warranty coverage, and return procedures without database access.

### 2. Deterministic Order Support Engine
Direct customer order queries invoke four deterministic read-only database tools:
1. `get_orders_by_customer_id`: Retrieves recent orders belonging strictly to the authenticated customer ID.
2. `get_order_details_by_id`: Retrieves granular line items, pricing breakdown, and delivery snapshot for a specific order owned by the customer.
3. `get_orders_by_product_name`: Filters customer orders containing specific items or product names.
4. `get_orders_by_order_number_and_customer_id`: Exact lookup verifying customer ownership.

> **Zero Hallucination Guarantee:** The LLM is never given direct SQL access or unstructured tabular dumps. It receives structured Pydantic tool responses and formats them into helpful, empathetic customer responses.

### 3. Secure Guest Verification & Reliable Outbox Worker
Customers who complete guest checkouts without an account can inspect orders securely:
- **Two-Step OTP Flow**: The guest provides `order_number` and checkout `email`. The system generates a cryptographically secure 6-digit numeric OTP, storing its SHA-256 hash with a 10-minute expiry window.
- **Scoped Guest Tokens**: Successful OTP validation issues a short-lived (15-minute) HMAC-SHA256 guest token strictly scoped to that single order. Guest tokens are rejected on customer account routes.
- **Reliable Asynchronous Email Outbox**: Instead of synchronous SMTP calls, OTP emails are written transactionally to the `guest_email_outbox` table. A dedicated background worker (`outbox_worker.py`) polls and dispatches emails with exponential backoff and dead-letter protection.

### 4. Customer Authentication & Authorization
- **Argon2id Password Hashing**: Modern, memory-hard hashing resistant to GPU cracking.
- **JWT Access Tokens**: Cryptographically signed bearer tokens with configurable expiration (15 minutes default).
- **Enforced Registration Validation**: Minimum 12-character passwords and mandatory display names.
- **Complete Customer Isolation**: All data queries enforce `customer_id == current_customer.id` at the database query level.

### 5. Production Hardening (Phase 1)
- **Distributed Sliding-Window Rate Limiting (M-1)**:
  - Protects `POST /api/v1/auth/login` (5 req/min per IP, 10 req/min per email) and `POST /api/v1/auth/register` (3 req/min per IP).
  - Backed by PostgreSQL table `auth_rate_limits` (Alembic migration `0007_auth_rate_limits.py`).
  - Safe across multi-worker deployments; verified with high-concurrency 20-thread barrier tests.
  - Returns `429 Too Many Requests` with RFC-compliant `Retry-After` header.
  - Immune to account enumeration; sanitizes reverse-proxy headers (`X-Forwarded-For`, `CF-Connecting-IP`).
- **Strict Origin CORS Allowlisting (H-1)**:
  - Configurable `ALLOWED_ORIGINS` via environment variable.
  - Strict origin matching; explicitly prohibits wildcard `*` when credentials or authorization headers are permitted.
- **Enterprise Security Headers Middleware (L-1)**:
  - `Strict-Transport-Security: max-age=31536000; includeSubDomains` (HTTPS-aware).
  - `Content-Security-Policy`: Protects against injection attacks while permitting necessary Google Fonts and Vite scripts.
  - `X-Content-Type-Options: nosniff`.
  - `X-Frame-Options: DENY` (anti-clickjacking).
  - `Referrer-Policy: strict-origin-when-cross-origin`.
  - `X-XSS-Protection: 1; mode=block`.

### 6. StoreFlow Customer Frontend (SPA)
Located in `frontend/`, built with modern React 18, Vite 6, and Tailwind CSS:
- **Official Brand Design**: Strict adherence to StoreFlow's official palette — Navy (`#0F172A`), Emerald (`#10B981`), and Slate (`#64748B`).
- **Responsive Split Layout**:
  - **Desktop**: 60/40 split with interactive Support Chat on the left and live Order Inspector on the right.
  - **Tablet**: Stacked layout with collapsible order details.
  - **Mobile**: Full-width support chat with dedicated modal order inspector.
- **Interactive Chat Assistant**: Quick-prompt action chips, supervisor routing badges, and clickable order-number chips that immediately populate the Order Inspector.
- **Order Inspector Panel**: Real-time status badges, itemized product tables, subtotal/shipping/tax breakdown, and delivery address snapshots.
- **Guest Order Portal**: Complete 3-step verification modal wizard with state isolation.

---

## Technology Stack

| Layer | Technologies |
|---|---|
| **Frontend** | React 18, Vite 6, TypeScript 5, Tailwind CSS 3, Lucide React, Vitest |
| **Backend API** | FastAPI, Starlette, Pydantic v2, Pydantic Settings, Uvicorn |
| **AI / LLM** | Google Gemini, `google-genai` Python SDK (`gemini-2.5-flash`) |
| **Database & ORM** | PostgreSQL 14+, SQLAlchemy 2.0, Psycopg 3, Alembic |
| **Authentication & Security** | Passlib (Argon2id), PyJWT, Cryptography, Sliding-Window DB Limiter |
| **Testing** | Pytest, Pytest-Asyncio, HTTPX, Vitest, React Testing Library |

---

## Database Migrations & Schema

Database migrations are managed via Alembic:

| Revision | Migration Name | Description |
|---|---|---|
| `0001` | `initial_schema` | Users, customer profiles, products, categories, inventory, addresses |
| `0002` | `orders_and_order_items` | Orders, order items, status enum, payment status, tracking |
| `0003` | `guest_orders` | Guest checkout associations, guest contact snapshots |
| `0004` | `guest_verifications` | SHA-256 hashed 6-digit OTP codes, verification attempts, expiry |
| `0005` | `guest_email_outbox` | Transactional email outbox table for resilient dispatch |
| `0006` | `indexes_and_optimizations` | Compound indexes on customer orders, SKUs, and status queries |
| `0007` | `auth_rate_limits` | Sliding-window request counter table with timestamp indexing |

Apply all migrations from the root directory:

```powershell
python -m alembic -c alembic.ini upgrade head
```

---

## API Specification

### Authentication & Customers
- `POST /api/v1/auth/register` — Register a customer (`email`, `password`, `display_name`). Rate-limited.
- `POST /api/v1/auth/login` — Sign in and obtain JWT access token (`email`, `password`). Rate-limited.
- `GET /api/v1/customers/me/profile` — Retrieve current customer profile (Bearer token required).
- `GET, POST /api/v1/customers/me/addresses` — Manage customer shipping addresses.

### AI Supervisor Chat
- `POST /api/v1/chat/ask` — Intelligent routing endpoint. Accepts `{"question": "..."}` with customer Bearer token. Returns categorized response (`destination`: `order_support` | `shopping_agent` | `general_assistant`).

### Customer Orders
- `GET /api/v1/orders` — List orders for authenticated customer.
- `GET /api/v1/orders/{order_id}` — Get full details, line items, and delivery address for a specific order.

### Guest Order Verification
- `POST /api/v1/guest/orders/request-otp` — Request 6-digit OTP code (`{"order_number": "...", "email": "..."}`).
- `POST /api/v1/guest/orders/verify-otp` — Verify code (`{"order_number": "...", "email": "...", "otp": "..."}`). Returns scoped guest JWT token.
- `GET /api/v1/guest/orders/{order_number}` — View verified guest order details (Requires guest Bearer token).

### Public Catalog
- `GET /api/v1/products` — Browse products with pagination.
- `GET /api/v1/products/{product_id}` — Product details.
- `GET /api/v1/categories` — Browse categories.

---

## Local Development & Setup

### Prerequisites
- Python 3.10+
- Node.js 18+ and npm
- PostgreSQL 14+
- Google Gemini API Key

### 1. Backend Setup

```powershell
# Create and activate Python virtual environment
py -m venv .venv
.\.venv\Scripts\Activate.ps1

# Install backend dependencies
python -m pip install -r requirements.txt -r requirements-dev.txt

# Configure environment variables
Copy-Item .env.example .env
```

Edit `.env` with your credentials:
```env
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-2.5-flash
DATABASE_URL=postgresql+psycopg://ecommerce_app:<password>@localhost:5432/ecommerce_agent
AUTH_SECRET_KEY=your_random_32_byte_secret_key
ACCESS_TOKEN_EXPIRE_MINUTES=15
ALLOWED_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
```

Run Alembic migrations:
```powershell
python -m alembic -c alembic.ini upgrade head
```

Start the FastAPI application:
```powershell
python -m uvicorn backend.app.main:app --reload --port 8000
```
- API Documentation: `http://localhost:8000/docs`
- Health Check: `http://localhost:8000/health`

### 2. Frontend Setup

```powershell
# Install frontend dependencies
npm install --prefix frontend

# Start the Vite development server
npm run dev --prefix frontend
```
The StoreFlow application will open at `http://localhost:5173`.

---

## Testing & Quality Assurance

Both backend and frontend feature automated test suites:

### Backend Test Suite (312 Tests)
```powershell
python -m pytest -q
```
Coverage includes:
- Unit & integration tests for all auth, customer, catalogue, order, and guest endpoints.
- Gemini supervisor mock tests & prompt routing validations.
- Alembic migration upgrade/downgrade validation (`0001` through `0007`).
- Rate limiter sliding-window algorithm, proxy header sanitization, and database concurrency.
- Security headers and CORS origin validation.

### Frontend Test Suite (24 Tests)
```powershell
npm test --prefix frontend -- --run
```
Coverage includes:
- `api_client.test.ts`: Request construction, token attachment, error wrapping.
- `auth_modal.test.tsx`: Sign in, registration, display name validation, password constraints.
- `chat_container.test.tsx` & `chat_components.test.tsx`: Supervisor routing display, order chips, product cards.
- `guest_portal.test.tsx`: Complete 3-step guest verification wizard and token isolation.
- `order_list.test.tsx` & `order_inspector.test.tsx`: Order selection, financial breakdown, item rendering.

### Production Build Verification
```powershell
npm run build --prefix frontend
```

---

## Security & Isolation Guarantees

1. **Cross-Customer Data Isolation**: Queries filter by authenticated user ID; one customer can never view another customer's orders.
2. **Customer / Guest Token Separation**: Guest tokens cannot access customer endpoints, and customer tokens cannot access guest routes.
3. **No Password Hashes Returned**: API models explicitly omit password hashes and cryptographic secrets.
4. **No Plaintext OTPs**: OTP codes are hashed with SHA-256 before storage in PostgreSQL.
5. **Rate Limiting**: Brute-force protection on authentication routes stops credential stuffing.
6. **Strict CORS & Headers**: Prevents unauthorized cross-origin requests, MIME-sniffing, clickjacking, and script injection.

---

<p align="center">
  <strong>StoreFlow</strong> — Designed and built for secure, scalable ecommerce customer support.
</p>
