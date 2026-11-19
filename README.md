# Ecommerce Agent

The backend provides a FastAPI question-and-answer API powered exclusively by
Google Gemini through the official `google-genai` Python SDK. It does not expose
customer, order, payment, or inventory data.

## Requirements

- Python 3.10 or later
- A Google Gemini API key for live requests

## Setup

From the repository root, create and activate a virtual environment, then
install the project dependencies:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Set `GEMINI_API_KEY` in `.env`. `GEMINI_MODEL` defaults to
`gemini-2.5-flash` and can be changed there.

## Run the API

```powershell
python -m uvicorn backend.app.main:app --reload
```

- Health check: `GET http://127.0.0.1:8000/health`
- Ask a general question: `POST http://127.0.0.1:8000/api/v1/ask`
- Interactive API documentation: `http://127.0.0.1:8000/docs`

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

The automated tests mock the Gemini client and do not consume API credits.

## Legacy order data

`orders.xlsx` and `mcp_server.py` are retained for the existing order-support
work. The Phase 2 API and Streamlit assistant do not connect to that MCP server
or return its customer data. The legacy MCP tools are not authenticated and
must not be exposed as a network service or used with real customer data until
an authorization layer is implemented.
