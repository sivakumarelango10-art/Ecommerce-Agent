# StoreFlow — Controlled Staging Deployment Guide

Comprehensive deployment specification and operational runbook for running **StoreFlow** in a staging environment.

- **Frontend Hosting:** Vercel (Single-Page Application)
- **Backend Hosting:** Render (Containerized FastAPI Web Service)
- **Database Engine:** Neon Serverless PostgreSQL
- **AI Engine:** Google Gemini (google-genai SDK)

---

## 1. Overview & Architecture

StoreFlow is an AI-powered conversational e-commerce and order support platform. The staging deployment architecture separates presentation, API computation, state storage, and artificial intelligence into dedicated cloud tiers:

```mermaid
flowchart TD
    User["End User / Browser"] -->|"HTTPS / WSS"| Vercel["Vercel Edge Network<br/>(React 18 + Vite SPA)"]
    Vercel -->|"REST API (JSON)<br/>VITE_API_BASE_URL"| Render["Render Web Service<br/>(FastAPI / Uvicorn)"]
    Render -->|"SQL / Transaction Pooler<br/>Port 5432 / 6543"| NeonPooler["Neon Connection Pooler<br/>(Runtime Queries)"]
    Render -.->|"Alembic DDL (Direct)<br/>DATABASE_URL_UNPOOLED"| NeonDirect["Neon Direct Endpoint<br/>(Schema Migrations)"]
    NeonPooler --> NeonDB[("Neon Staging Postgres DB<br/>Revisions 0001–0007")]
    NeonDirect --> NeonDB
    Render -->|"Google GenAI API"| GeminiAPI["Google Gemini 2.5/Flash"]
    Render -->|"SMTP (TLS / Port 587)"| SMTPServer["Outbox Email Delivery Provider"]
```

### Architectural Principles
1. **Decoupled Edge Frontend:** Built with React 18, Vite 6, Tailwind CSS, and TypeScript 5. Served globally via Vercel Edge CDN with strict Content Security Policy (CSP) and HTTP security headers.
2. **Stateless Scalable Backend:** Packaged as a Python FastAPI application executed by Uvicorn. Operates behind Render's managed reverse proxy with `--proxy-headers` for trusted IP forwarding.
3. **Dedicated Serverless Storage:** Hosted on Neon PostgreSQL. Utilizes connection pooling for high-concurrency application workers and dedicated direct endpoints for transactional DDL migrations via Alembic.
4. **Resilient Asynchronous Outbox:** Background email delivery runs in-process inside the FastAPI application lifespan with database lease tokens, preventing duplicate sends across worker restarts.

---

## 2. Prerequisites & Tooling

Before provisioning staging, verify the following prerequisites:

| Requirement | Version / Specification | Purpose |
| :--- | :--- | :--- |
| **Git** | `>= 2.40` | Source control and release tagging |
| **Node.js** | `>= 20.x LTS` (npm `>= 10.x`) | Frontend build verification |
| **Python** | `3.11` or `3.12` | Backend runtime & test suite |
| **Neon CLI** | `neonctl` or `neon` (`@latest`) | Branch & database lifecycle management |
| **Render Account** | Team or Individual with Blueprint support | Backend web service hosting |
| **Vercel Account** | Team or Hobby account | Frontend CDN hosting |
| **Google Gemini API Key** | `AI Studio` or `Google Cloud Vertex AI` | Natural language processing |

---

## 3. Official StoreFlow Branding Guidelines

StoreFlow branding is standardized and must be maintained across all staging and production deployments.

- **Primary Logo File:** `frontend/public/storeflow-logo.svg`
- **Logo Proportions:** `viewBox="0 0 340 80"`, display width `680px`, display height `160px`
- **Official Tagline:** `Order Support & Shopping Agent`
- **Official Color Palette:**
  - **Navy (Primary Dark):** `#0F172A` (Background tile & primary brand typography)
  - **Emerald (Accent & Brand Highlight):** `#10B981` (Shopping flow wave, sparkles, "Flow" wordmark)
  - **Slate (Subtitles & Muted UI):** `#64748B` (Tagline & secondary metadata)
  - **Neutral Light:** `#F8FAFC` (Page background)
- **Favicon & Title:** Served via `<link rel="icon" type="image/svg+xml" href="/storeflow-logo.svg" />` and `<title>StoreFlow — Order Support &amp; Shopping Agent</title>`.

> [!IMPORTANT]
> Never replace `storeflow-logo.svg` with placeholder text, alternative geometric shapes, or conflicting brand names.

---

## 4. Neon Database Preparation

The staging database must run on an isolated branch within your Neon project to ensure full separation from development, testing, and production.

### Step 4.1: Branch Provisioning
Create a dedicated `staging` branch from the root or clean parent:
```bash
# Using Neon CLI
neon branches create --name staging
```
Alternatively, in the Neon Console:
1. Navigate to **Branches** -> **New Branch**.
2. Name the branch `staging`.
3. Select the latest stable snapshot or empty base.

> [!CAUTION]
> **Data Isolation Rule:** Never configure staging to point to `ecommerce_agent_dev` or `ecommerce_agent_test`. All staging operations must target the dedicated Neon `staging` branch and isolated database credentials.

### Step 4.2: Connection Strings Configuration
Neon provides two connection strings for every branch:
1. **Pooled Connection String (`DATABASE_URL`):**
   - Format: `postgresql+psycopg://[user]:[password]@ep-[name]-pooler.[region].aws.neon.tech/[dbname]?sslmode=require`
   - Role: Used by FastAPI runtime for transaction pooling and connection efficiency.
2. **Direct / Unpooled Connection String (`DATABASE_URL_UNPOOLED`):**
   - Format: `postgresql+psycopg://[user]:[password]@ep-[name].[region].aws.neon.tech/[dbname]?sslmode=require`
   - Role: Used exclusively by Alembic for DDL migrations (`CREATE TABLE`, `ALTER TABLE`, index creation).

### Step 4.3: Migration Verification
The staging database schema includes all 7 revisions:
- `0001_database_foundation`: Core customer accounts, products, inventory, orders, and payment records.
- `0002_normalize_check_constraint_names`: Canonical naming for check constraints.
- `0003_cart_and_orders`: Persistent shopping carts, cart items, order items, and shipping records.
- `0004_order_request_fingerprint`: Idempotency keys and fingerprint tracking for checkout operations.
- `0005_guest_order_verification`: Guest checkout verification, OTP tokens, and email outbox table.
- `0006_outbox_lease_token`: Distributed worker lease tokens for outbox dispatch.
- `0007_auth_rate_limits`: Concurrency-safe rate limit tracking table for authentication endpoints.

---

## 5. Render Backend Deployment

Render hosts the backend web service using the repository's `render.yaml` Blueprint.

### Step 5.1: Blueprint Configuration (`render.yaml`)
The web service is defined declaratively in `render.yaml`:
```yaml
services:
  - type: web
    name: storeflow-staging-backend
    runtime: python
    buildCommand: pip install -r requirements.txt
    startCommand: uvicorn backend.app.main:app --host 0.0.0.0 --port $PORT --proxy-headers
    healthCheckPath: /health
    autoDeploy: false
```

### Key Render Settings
- **Root Directory:** Repository root (`.`).
- **Build Command:** `pip install -r requirements.txt` (installs backend dependencies, Alembic, uvicorn, and utilities).
- **Pre-Deploy Command:** Omitted (`preDeployCommand` is not available on Render's Free tier). Schema migrations must be executed manually against the Neon staging database prior to triggering deployments.
- **Start Command:** `uvicorn backend.app.main:app --host 0.0.0.0 --port $PORT --proxy-headers`
  - Binds to dynamic environment port `$PORT`.
  - Enables `--proxy-headers` so FastAPI correctly resolves upstream client IPs from Render's load balancers.
- **Health Check Path:** `/health` (returns `{"status": "ok"}` with HTTP 200).
- **Worker Processes:** No background worker service is needed; the `GuestEmailOutboxWorker` runs within the FastAPI application lifespan.

---

## 6. Vercel Frontend Deployment

Vercel hosts the static React Single-Page Application (SPA) compiled with Vite.

### Step 6.1: Vercel Configuration (`frontend/vercel.json`)
The application requires SPA path rewrites and HTTP security headers:
```json
{
  "$schema": "https://openapi.vercel.sh/vercel.json",
  "cleanUrls": true,
  "rewrites": [
    {
      "source": "/(.*)",
      "destination": "/index.html"
    }
  ],
  "headers": [
    {
      "source": "/(.*)",
      "headers": [
        { "key": "X-Content-Type-Options", "value": "nosniff" },
        { "key": "X-Frame-Options", "value": "DENY" },
        { "key": "Referrer-Policy", "value": "strict-origin-when-cross-origin" },
        { "key": "Strict-Transport-Security", "value": "max-age=31536000; includeSubDomains" },
        {
          "key": "Content-Security-Policy",
          "value": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data: https:; connect-src 'self' https://*.onrender.com https://your-backend-render-service.onrender.com; frame-ancestors 'none';"
        }
      ]
    }
  ]
}
```

### Step 6.2: Project Settings in Vercel Console
1. **Root Directory:** `frontend`
2. **Framework Preset:** `Vite`
3. **Build Command:** `npm run build` (or `tsc && vite build`)
4. **Output Directory:** `dist`
5. **Install Command:** `npm ci`
6. **Environment Variables:**
   - `VITE_API_BASE_URL`: `https://<your-render-backend-name>.onrender.com` (do not include a trailing slash).

---

## 7. Staging Environment Variables Checklist

The backend configuration parameters and the frontend environment variable are documented below:

### Backend Variables (Render Environment)

| Variable Name | Required | Default | Recommended Staging Value | Description & Purpose |
| :--- | :---: | :--- | :--- | :--- |
| `ENVIRONMENT` | Yes | `development` | `staging` | Runtime environment name (`staging`/`production` enforces `USER_KEY_ENCRYPTION_SECRET`). |
| `DATABASE_URL` | Yes | — | `postgresql+psycopg://...@ep-...-pooler.../neondb?sslmode=require` | Pooled Neon PostgreSQL connection URL. |
| `DATABASE_URL_UNPOOLED` | Yes | `None` | `postgresql+psycopg://...@ep-...aws.neon.tech/neondb?sslmode=require` | Direct Neon PostgreSQL connection URL for Alembic migrations. |
| `AUTH_SECRET_KEY` | Yes | — | Cryptographically random string (`>= 32 bytes`) | Secret key used for signing customer and guest JWT tokens. |
| `ACCESS_TOKEN_EXPIRE_MINUTES`| No | `15` | `15` | Customer session JWT expiration duration. |
| `GUEST_TOKEN_EXPIRE_MINUTES` | No | `15` | `15` | Guest order verification JWT expiration duration. |
| `OTP_HASH_SECRET` | Recommended | `None` | Cryptographically random string (`>= 16 bytes`) | Salt secret for hashing OTP verification codes in database. |
| `USER_KEY_ENCRYPTION_SECRET` | Yes (`staging`/`production`) | `None` | Cryptographically random string (`>= 32 bytes`), distinct from `AUTH_SECRET_KEY` and `GEMINI_API_KEY` | Required symmetric encryption key for customer personal Gemini API keys in `staging` and `production`. |
| `PLATFORM_KEY_ENCRYPTION_SECRET` | Optional (AI Key Vault) | `None` | Cryptographically random string (`>= 32 bytes`), distinct from `AUTH_SECRET_KEY`, `USER_KEY_ENCRYPTION_SECRET`, and `GEMINI_API_KEY` | Optional encryption key for Super Admin stored Platform AI Provider keys in database vault. |
| `ADMIN_EMAILS` | Yes (for admin bootstrap) | `set()` | Comma-separated or JSON list of normalized super-admin emails | Authorized administrator emails recognized during login/bootstrap. |
| `GEMINI_API_KEY` | Yes | — | Valid Google AI Studio API key | API key for Gemini assistant models. |
| `GEMINI_MODEL` | No | `gemini-3.8-flash` | `gemini-3.8-flash` (or `gemini-2.5-flash`) | Gemini model identifier. |
| `GEMINI_TIMEOUT_SECONDS` | No | `30` | `30` | Timeout in seconds for upstream Gemini API calls. |
| `CATALOGUE_CURRENCY` | No | `USD` | `USD` | ISO 4217 3-letter currency code for prices. |
| `ALLOWED_ORIGINS` | Yes | Localhost | `["https://<your-vercel-domain>.vercel.app"]` | JSON array or comma-separated list of explicit frontend origins. Wildcards (`*`) are rejected. |
| `AUTH_RATE_LIMIT_LOGIN_PER_MINUTE` | No | `10` | `10` | Maximum login attempts allowed per IP/email per minute. |
| `AUTH_RATE_LIMIT_REGISTER_PER_MINUTE` | No | `5` | `5` | Maximum registration attempts allowed per IP per minute. |
| `AUTH_RATE_LIMIT_WINDOW_SECONDS` | No | `60` | `60` | Sliding window in seconds for auth rate limit tracking. |
| `OUTBOX_WORKER_ENABLED` | No | `True` | `true` | Enables in-process email delivery outbox worker. |
| `OUTBOX_POLL_INTERVAL_SECONDS` | No | `2.0` | `2.0` | Polling frequency in seconds for outbox queue. |
| `OUTBOX_LEASE_SECONDS` | No | `60` | `60` | Lease duration to lock outbox items during dispatch. |
| `OUTBOX_BATCH_SIZE` | No | `10` | `10` | Maximum number of emails processed per polling tick. |
| `SMTP_HOST` | Staging | `None` | SMTP host (e.g. `smtp.sendgrid.net`, `smtp.mailgun.org`) | Outgoing mail server for verification OTP emails. |
| `SMTP_PORT` | Staging | `587` | `587` | Outgoing SMTP server port. |
| `SMTP_USERNAME` | Staging | `None` | SMTP user / API token key | Credentials for authenticating with SMTP provider. |
| `SMTP_PASSWORD` | Staging | `None` | SMTP password / API token secret | Secret key for SMTP provider. |
| `SMTP_FROM_EMAIL` | Staging | `None` | `no-reply@yourdomain.com` | Verified sender email address. |
| `SMTP_USE_TLS` | No | `True` | `true` | Enables STARTTLS encryption for SMTP. |
| `SMTP_USE_SSL` | No | `False` | `false` | Enables direct SSL encryption (Port 465). |

### Frontend Variables (Vercel Environment)

| Variable Name | Required | Example Staging Value | Description |
| :--- | :---: | :--- | :--- |
| `VITE_API_BASE_URL` | Yes | `https://storeflow-staging-backend.onrender.com` | Public base URL of Render backend service without trailing slash. |

---

## 8. Security & CORS Synchronization

1. **Origin Verification:**
   - FastAPI strictly enforces `ALLOWED_ORIGINS`.
   - Wildcards (`*`) combined with credentials (`allow_credentials=True`) are strictly forbidden and rejected at configuration startup.
   - Configure the Render environment variable `ALLOWED_ORIGINS` to contain the exact staging Vercel URL:
     `ALLOWED_ORIGINS=["https://storeflow-staging.vercel.app"]`
2. **CSP Synchronization:**
   - Vercel's `connect-src` header in `vercel.json` must permit connections to the Render domain (`https://*.onrender.com` or explicit custom domain).
   - If a custom staging domain is used, add both origins to `ALLOWED_ORIGINS` and `connect-src`.
3. **HTTP Strict Transport Security (HSTS):**
   - Configured with `max-age=31536000; includeSubDomains` on Vercel.
4. **Frame Ancestors:**
   - Both backend and frontend reject embedding within external iframes (`X-Frame-Options: DENY`, `frame-ancestors 'none'`).

---

## 9. Reverse Proxy & Rate Limiting

The authentication endpoints (`/api/v1/auth/login`, `/api/v1/auth/register`) utilize a concurrency-safe PostgreSQL rate limiter backed by the `auth_rate_limits` table:

1. **Client IP Extraction:**
   - In `backend/app/security/rate_limiter.py`, incoming requests check headers in order:
     1. `CF-Connecting-IP` (if Cloudflare proxying is enabled).
     2. `X-Forwarded-For` (first IP in chain).
     3. `X-Real-IP`.
     4. `request.client.host`.
2. **Uvicorn Proxy Headers:**
   - The `--proxy-headers` flag is enabled in Render's `startCommand`, ensuring Uvicorn respects forwarded protocol and host metadata.
3. **Privacy Protection:**
   - IPs and email addresses are hashed with SHA-256 before storage in `auth_rate_limits`. Plaintext addresses or passwords are never logged or stored.
4. **429 Response & Retry-After:**
   - When requests exceed limits, a 429 Too Many Requests response is returned with a `Retry-After: <seconds>` header.
   - The check occurs before account lookup, preventing account enumeration.

---

## 10. Background Outbox Worker Operations

The guest verification system utilizes a transactional outbox pattern to decouple HTTP request latency from email delivery:

1. **Lifespan Execution:** The worker is managed in `backend/app/main.py` via FastAPI's async context manager. No standalone worker process or Celery/Redis daemon is required.
2. **Concurrency Safety:** Each worker claims outbox items by setting a unique `lease_token` and `leased_until` timestamp using an atomic `UPDATE ... WHERE leased_until < NOW() OR leased_until IS NULL` transaction.
3. **Failure Backoff:** Unsent items are retried up to 5 times with exponential backoff before being marked as dead-lettered.
4. **In-Memory Fallback:** If SMTP settings are omitted in development or staging, the application logs OTP codes safely to standard error without crashing.
5. **Render Free Outbound SMTP Restriction & Alternatives:**
   > [!WARNING]
   > Render Free web services block all outbound raw TCP traffic on SMTP ports (`25`, `465`, `587`) to protect network reputation and prevent spam abuse. Attempting to connect directly to an external SMTP host over port 587 or 465 from a Render Free instance will encounter network timeouts or connection refused errors.
   >
   > **Recommended Staging Alternatives:**
   > - **Omit SMTP Settings (Safe Logging Fallback):** Leave `SMTP_HOST` unset or empty in the Render staging environment. StoreFlow will safely output OTP verification codes to server logs, allowing testers to inspect logs in the Render dashboard and retrieve the OTP for staging verification.
   > - **HTTP Transactional Email API:** Use an email provider that operates via HTTPS REST APIs (e.g., SendGrid Web API, Resend, Mailgun HTTP API, Postmark API over port `443`), which is fully permitted on Render Free.
   > - **Render Paid Plan:** Upgrading to a paid instance tier (Starter plan or above) unblocks outbound SMTP ports if direct raw TCP SMTP delivery is necessary.

---

## 11. Database Migration Operations

Because the selected Render staging service runs on the Free tier (which does not support automated `preDeployCommand` execution), all schema migrations must be run **manually** against the isolated Neon staging database before starting or deploying a new backend revision.

> [!CAUTION]
> **CRITICAL DATA ISOLATION WARNING:** Under NO circumstances should `ecommerce_agent_dev` or `ecommerce_agent_test` ever be targeted or used for staging migrations. Staging migrations must strictly target the isolated Neon staging branch endpoint.

### Manual Migration Procedure

Alembic migrations must be executed using the direct, unpooled connection string (`DATABASE_URL_UNPOOLED`) to support transactional DDL on Neon:

```bash
# 1. From an authorized administrative workstation or secure runner, set the unpooled staging URL:
export DATABASE_URL_UNPOOLED="postgresql+psycopg://[user]:[password]@ep-[staging-endpoint].aws.neon.tech/neondb?sslmode=require"

# 2. Check current migration state against the staging database:
alembic current

# 3. Apply all pending migrations (revisions 0001 through 0007) to the staging database:
alembic upgrade head

# 4. Verify head status:
alembic current
```

If running from PowerShell on Windows:
```powershell
$env:DATABASE_URL_UNPOOLED = "postgresql+psycopg://[user]:[password]@ep-[staging-endpoint].aws.neon.tech/neondb?sslmode=require"
alembic upgrade head
```

> [!IMPORTANT]
> Always execute `alembic upgrade head` before promoting or triggering a new backend build on Render Free. Once migrations complete successfully, trigger the deployment in Render.

---

## 12. Post-Deployment Verification & Smoke Tests

After deploying both tiers, run the following verification checks:

### Step 12.1: Health Endpoint Check
```bash
curl -i https://<your-render-backend>.onrender.com/health
```
**Expected Response:** HTTP 200 OK with `{"status": "ok"}`.

### Step 12.2: CORS Preflight Verification
```bash
curl -i -X OPTIONS https://<your-render-backend>.onrender.com/api/v1/auth/login \
  -H "Origin: https://<your-vercel-domain>.vercel.app" \
  -H "Access-Control-Request-Method: POST" \
  -H "Access-Control-Request-Headers: Content-Type,Authorization"
```
**Expected Response:** HTTP 200 OK with `Access-Control-Allow-Origin: https://<your-vercel-domain>.vercel.app` and `Access-Control-Allow-Credentials: true`.

### Step 12.3: Customer Authentication Flow
1. Open the Vercel staging URL in a browser.
2. Verify the official StoreFlow logo renders crisply in the navigation bar.
3. Navigate to Register and create a new customer account (`testuser@example.com`).
4. Log in and verify a valid JWT token is stored and authenticated state is reflected in the UI.

### Step 12.4: Guest Checkout & OTP Verification
1. Open an incognito browser window.
2. Add a product to the cart.
3. Proceed to Checkout as Guest.
4. Submit guest email. Verify that an OTP email is dispatched (or logged if using staging sandbox).
5. Enter OTP code. Verify successful issuance of guest order verification token.

### Step 12.5: AI Shopping Assistant Test
1. Send a query in the chat assistant (e.g., "Find wireless headphones under $100").
2. Verify the streaming or structured response returns product recommendations from the catalog.

---

## 13. Rollback & Disaster Recovery Runbook

| Incident Scenario | Action & Remediation Steps |
| :--- | :--- |
| **Frontend Regression** | In Vercel Dashboard -> **Deployments**, locate the prior successful deployment, click the `...` menu, and select **Promote to Production / Instant Rollback**. Rollback takes `< 5 seconds`. |
| **Backend API Crash** | In Render Dashboard -> **Deploys**, select the prior passing build and click **Rollback**. Render immediately reverts container traffic to the previous Docker image. |
| **Database Corruption / Schema Failure** | 1. In Render, pause the service or suspend traffic.<br/>2. If schema change is reversible, execute `alembic downgrade -1`.<br/>3. In Neon Console, use **Point-in-Time Restore** to create a branch from a snapshot prior to the migration failure. |
| **Rate Limit Lockout** | Connect to PostgreSQL with `psql` and truncate or prune rate limits: `DELETE FROM auth_rate_limits WHERE created_at < NOW();`. |

---

## 14. Production Promotion Readiness Criteria

Before promoting code from staging to production:

- [ ] All 312+ backend unit and integration tests pass cleanly (`pytest`).
- [ ] All 24+ frontend unit and UI tests pass cleanly (`vitest`).
- [ ] Frontend production build compiles without TypeScript or asset errors (`npm run build`).
- [ ] Database migrations 0001 through 0007 applied and verified.
- [ ] Official StoreFlow logo and brand palette verified across desktop and mobile screens.
- [ ] No hardcoded secrets, API keys, or `.env` files present in source code.
- [ ] Rate limiting verified against simultaneous login attempts.
- [ ] CORS origins restricted exclusively to official production domains.
- [ ] Database connection pooling verified under concurrent load.
- [ ] Outbox email delivery verified with production SMTP provider.
