import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings:
    app_name: str = os.getenv("APP_NAME", "Oppi CRM Comercial")
    app_secret_key: str = os.getenv("APP_SECRET_KEY", "change-me-in-production")
    database_url: str = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'oppi_crm.db'}")
    session_timeout_minutes: int = int(os.getenv("SESSION_TIMEOUT_MINUTES", "480"))
    timezone: str = os.getenv("APP_TIMEZONE", "America/Sao_Paulo")
    currency: str = os.getenv("APP_CURRENCY", "BRL")

    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    whatsapp_api_url: str = os.getenv("WHATSAPP_API_URL", "")
    whatsapp_api_token: str = os.getenv("WHATSAPP_API_TOKEN", "")

    n8n_webhook_url: str = os.getenv("N8N_WEBHOOK_URL", "")

    asaas_api_key: str = os.getenv("ASAAS_API_KEY", "")
    asaas_api_url: str = os.getenv("ASAAS_API_URL", "https://api.asaas.com/v3")

    zapsign_api_token: str = os.getenv("ZAPSIGN_API_TOKEN", "")
    zapsign_api_url: str = os.getenv("ZAPSIGN_API_URL", "https://api.zapsign.com.br/api/v1")

    smtp_host: str = os.getenv("SMTP_HOST", "")
    smtp_port: int = int(os.getenv("SMTP_PORT", "587"))
    smtp_user: str = os.getenv("SMTP_USER", "")
    smtp_password: str = os.getenv("SMTP_PASSWORD", "")

    google_sheets_credentials: str = os.getenv("GCP_SERVICE_ACCOUNT_B64", "")

    run_seed_on_startup: bool = os.getenv("RUN_SEED_ON_STARTUP", "false").lower() == "true"
    proposals_dir: Path = BASE_DIR / "generated" / "proposals"
    assets_dir: Path = BASE_DIR / "assets"


settings = Settings()
settings.proposals_dir.mkdir(parents=True, exist_ok=True)
