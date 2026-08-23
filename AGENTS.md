# AGENTS.md

## Cursor Cloud specific instructions

### What this repo is
The primary, deployed application is the **FastAPI app** `app.main:app` — "Dashboard Oppi
Comercial", a commercial CRM + WhatsApp attendance (Atendimentos) dashboard. Despite the
`README.md` wording, the deploy (`Dockerfile`, `docker-compose.yml`, `start.sh`,
`nixpacks.toml`, `DEPLOY_EASYPANEL.md`) runs FastAPI/uvicorn, NOT the Streamlit `app.py`.
The Streamlit `app.py` under the repo root is a secondary/legacy CRM and is not the deploy target.

### Dev environment
- Python deps live in a local virtualenv at `.venv` (see the startup update script). Run
  everything with `.venv/bin/...` (e.g. `.venv/bin/uvicorn`, `.venv/bin/python`, `.venv/bin/pytest`).
- Runtime config is read from a `.env` file at the repo root (gitignored). Auth is required:
  set `APP_USERNAME` and `APP_PASSWORD` (dev login used here: `oppitech` / `100316*`, per
  `DEPLOY_EASYPANEL.md`). `SESSION_SECRET` defaults to `APP_PASSWORD` if unset.

### Run the app (dev)
- `.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8501 --reload`
- Health check: `GET /health` → `{"status":"ok", ...}`. Root `/` redirects to `/visao-geral`.

### Tests / lint
- Tests: `.venv/bin/python -m pytest tests/` (15 tests; `pytest` is a dev-only dep, not in
  `requirements.txt`). Some tests also run standalone via `unittest`.
- No linter/formatter is configured in this repo (no ruff/flake8/black config), even though
  `.gitignore` mentions `.ruff_cache`.

### Data sources & what works WITHOUT external credentials (important)
- The CRM data source is **Google Sheets** (`GCP_SERVICE_ACCOUNT_B64`). The relational DB
  (`DATABASE_URL`) is optional and **falls back to local SQLite** when unset
  (`oppi_crm.db` / `storage/crm_local.db`).
- Without `GCP_SERVICE_ACCOUNT_B64`, the CRM/dashboard pages that build filter options from
  sheet data (`/visao-geral`, `/propostas`, `/leads-e-empresas`, `/atividades`,
  `/metas-e-relatorios`) return HTTP 500 because the prepared DataFrame is empty
  (`KeyError: '_nicho'` in `app/services/filters.get_filter_options`). This is expected with
  no data configured — it is NOT an environment breakage.
- Pages that DO work with no external secrets: `/login`, `/atendimentos` (WhatsApp inbox),
  `/configuracoes` (settings), and the health endpoints.
- Background startup threads log Google Sheets `RuntimeError` (missing credentials) and CRM
  migration retries when Sheets is not configured. These are non-fatal and don't block boot.

### Exercising the WhatsApp inbox without the Evolution API (no secrets needed)
- Incoming messages arrive via `POST /webhooks/evolution`. When neither
  `EVOLUTION_WEBHOOK_TOKEN` nor `EVOLUTION_API_KEY` is set, the webhook authorizes by default,
  so you can simulate an inbound WhatsApp message to create a local conversation. Example
  payload: `{"event":"messages.upsert","instance":"oppi-comercial","data":{"key":{"remoteJid":"5511999998888@s.whatsapp.net","fromMe":false,"id":"X1"},"pushName":"Maria Teste","message":{"conversation":"Ola"},"messageType":"conversation","messageTimestamp":<unix_seconds>}}`.
- Inbound is processed asynchronously (ACK is immediate, a daemon thread saves the message).
  Verify with `GET /health/webhook` (`messages_saved`, `inbox.conversations`, `inbox.unread`).
- Do NOT test outbound sending without a real Evolution API — and note the WhatsApp SEND path
  is frozen per `.cursor/rules/nao-mexer-whatsapp-envio.mdc`; do not modify it.

### PDF engine
- Proposal PDFs use LibreOffice (`soffice`) in production (installed in the `Dockerfile`).
  It is NOT installed by the dev update script; `GET /health/pdf-engine` reports its status and
  the code falls back to a ReportLab path when LibreOffice is absent.
