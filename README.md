# Ecommerce Agent

The backend provides a FastAPI question-and-answer API powered exclusively by
Google Gemini through the official `google-genai` Python SDK. It does not expose
customer, order, payment, or inventory data.

## Requirements

- Python 3.10 or later
- PostgreSQL 14 or later for persistent application data
- A Google Gemini API key for live requests

## Setup

From the repository root, create and activate a virtual environment, install
the project dependencies, and create a local environment file:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Set these values in `.env`:

| Variable | Required | Description |
|---|---|---|
| `GEMINI_API_KEY` | Yes | Google Gemini API key. The official `google-genai` SDK is the only LLM integration. |
| `GEMINI_MODEL` | No | Gemini model name; defaults to `gemini-3.8-flash`. |
| `DATABASE_URL` | Yes | SQLAlchemy URL, for example `postgresql+psycopg://<user>:<password>@localhost:5432/ecommerce_agent`. |
| `AUTH_SECRET_KEY` | Yes | At least 32 bytes of random data used to sign access tokens. Generate locally with `python -c "import secrets; print(secrets.token_urlsafe(32))"`. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | No | Access-token lifetime; defaults to 15 minutes (maximum 1440). |

Keep `.env` private; it is ignored by Git. Do not paste credentials into source
files or commit them. For local PostgreSQL, create a database named
`ecommerce_agent` and a dedicated application role (for example, in `psql` or
pgAdmin):

```sql
CREATE ROLE ecommerce_app LOGIN PASSWORD '<choose-a-local-password>';
CREATE DATABASE ecommerce_agent OWNER ecommerce_app;
```

Set `DATABASE_URL` to
`postgresql+psycopg://ecommerce_app:<your-password>@localhost:5432/ecommerce_agent`.
SQLite URLs are also supported for automated tests and local development, but
PostgreSQL is the intended persistent database.

## Database migrations

With PostgreSQL running and `DATABASE_URL` configured in `.env`, apply the
schema from the repository root:

```powershell
python -m alembic -c alembic.ini upgrade head
```

Inspect the current migration revision or roll back the initial schema with:

```powershell
python -m alembic -c alembic.ini current
python -m alembic -c alembic.ini downgrade base
```

The initial migration creates users, customer profiles, products, categories,
product-category links, inventory, and customer addresses. It does not import,
modify, or delete `orders.xlsx`.

## Run the API

```powershell
python -m uvicorn backend.app.main:app --reload
```

- Health check: `GET http://127.0.0.1:8000/health`
- Ask a general question: `POST http://127.0.0.1:8000/api/v1/ask`
- Interactive API documentation: `http://127.0.0.1:8000/docs`

The customer and catalogue endpoints are:

| Method | Path | Access |
|---|---|---|
| `POST` | `/api/v1/auth/register` | Public |
| `POST` | `/api/v1/auth/login` | Public |
| `GET`, `PUT` | `/api/v1/customers/me/profile` | Bearer token; current customer only |
| `GET`, `POST` | `/api/v1/customers/me/addresses` | Bearer token; current customer only |
| `GET`, `PUT`, `DELETE` | `/api/v1/customers/me/addresses/{address_id}` | Bearer token; owner only |
| `GET` | `/api/v1/products`, `/api/v1/products/{product_id}` | Public catalogue |
| `GET` | `/api/v1/products/{product_id}/availability` | Public availability |
| `GET` | `/api/v1/categories` | Public catalogue |

Registration and login accept JSON. Registration requires `email`,
`password` (12–128 characters), and `display_name`; login requires `email` and
`password`. Send the returned access token as
`Authorization: Bearer <access_token>` to protected customer endpoints.
Customer APIs never return password hashes. There are no checkout, payment,
refund, or order APIs in this phase.

Example request:

```json
{
  "question": "What should I compare when choosing a laptop?"
}
```

The application validates its Gemini configuration during startup. The health
route is a liveness check and does not make a Gemini request.

## Run the Streamlit assistant

```powershell
streamlit run app.py
```

The Streamlit assistant uses the same Gemini service and only answers general
shopping questions.

## Tests

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

The automated tests use an isolated SQLite database and mock the Gemini client;
they do not require a live PostgreSQL server or consume Gemini API credits.
They cover registration/login, token validation, customer ownership, catalogue
and inventory reads, schema constraints, and SQLite migration upgrade/downgrade.
PostgreSQL-specific behavior should also be verified against a PostgreSQL
instance before deployment.

## Legacy order data

`orders.xlsx` and `mcp_server.py` are retained for the existing order-support
work. The Phase 2 API and Streamlit assistant do not connect to that MCP server
or return its customer data. The legacy MCP tools are not authenticated and
must not be exposed as a network service or used with real customer data until
an authorization layer is implemented.
