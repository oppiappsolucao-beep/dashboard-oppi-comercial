# AGENTS.md

## Cursor Cloud specific instructions

### What this repo is
The deployed product is the **FastAPI app** served from `app.main:app` ("Dashboard Oppi Comercial" — CRM + WhatsApp attendances). See `DEPLOY_EASYPANEL.md`. The Streamlit entrypoint `app.py` (and `views/`, `components/`, `auth/`, `config/settings.py`) is **legacy** and is not the deploy target — despite what `README.md` says, `Dockerfile`/`start.sh`/`nixpacks.toml` all run uvicorn on `app.main:app`. Don't spend effort running Streamlit unless explicitly asked.

Note: the WhatsApp / Atendimentos send logic is frozen per `.cursor/rules/nao-mexer-whatsapp-envio.mdc`. Do not touch it without an explicit request.

### Setup / dependencies
The startup update script (`pip install -r requirements.txt` + `pytest`) runs automatically before each session inside `.venv`. Activate it with `. .venv/bin/activate`. `python3-venv` is preinstalled on the VM. No lint tool is configured (no ruff/flake8/black/pre-commit).

### Running the app (dev)
- Activate venv, then: `uvicorn app.main:app --host 0.0.0.0 --port 8501 --reload`
- Needs `.env` at repo root (gitignored, not committed). Minimum for local dev:
  - `APP_USERNAME=oppitech`, `APP_PASSWORD=100316*` (login credentials; also in `DEPLOY_EASYPANEL.md`)
  - `SESSION_SECRET=<anything>`, `EVOLUTION_INSTANCE=oppi-dev` (gives the attendances inbox a line)
- Health check: `GET /health` returns `{"status":"ok"}`. Login at `/login`.

### Important dev-env caveats (non-obvious)
- **No Google Sheets creds locally.** Without `GCP_SERVICE_ACCOUNT_B64`, the CRM-data pages (`/visao-geral`, `/propostas`, `/atividades`, `/leads-e-empresas`, `/metas-e-relatorios`) return HTTP 500 (e.g. `KeyError: '_nicho'`). This is expected. Because `/` redirects to `/visao-geral`, the post-login landing page shows an error banner; navigate straight to `/atendimentos` or `/configuracoes`, which are backed by **local SQLite** and work fully offline.
- **DB fallback.** With no `DATABASE_URL`, the app uses SQLite (`oppi_crm.db` + `storage/crm_local.db`); `docker-compose.yml` wires Postgres for a full stack. SQLite `*.db-wal`/`*.db-shm` files appear at runtime; do not commit them.
- **Simulating inbound WhatsApp (no Evolution API needed):** POST an Evolution `messages.upsert` payload to `/webhooks/evolution` (no token required when `EVOLUTION_WEBHOOK_TOKEN`/`EVOLUTION_API_KEY` are unset). This creates a conversation that appears in `/atendimentos`. Useful for exercising the core inbox flow offline; `EVOLUTION_INSTANCE` in `.env` should match the payload's `instance`.

### Tests
`. .venv/bin/activate && python -m pytest tests/` runs 15 tests, all offline (they use a temp SQLite via `DATABASE_URL`).
