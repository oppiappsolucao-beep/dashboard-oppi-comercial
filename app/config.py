import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


class Settings:
    sheet_id: str = "1GAbrca0NSiJfPXaSte1qGxXCsGkQPacoRsm0PVB51gE"
    worksheet_name: str = "Folha1"
    cache_ttl_seconds: int = 120

    app_username: str
    app_password: str
    session_secret: str
    gcp_service_account_b64: str = ""

    scopes: list[str] = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    def __init__(self) -> None:
        self.app_username = os.getenv("APP_USERNAME", "").strip()
        self.app_password = os.getenv("APP_PASSWORD", "").strip()
        self.session_secret = os.getenv("SESSION_SECRET", "").strip()
        if not self.session_secret and self.app_password:
            self.session_secret = self.app_password
        self.gcp_service_account_b64 = (
            os.getenv("GCP_SERVICE_ACCOUNT_B64", "").strip()
            or os.getenv("GOOGLE_SERVICE_ACCOUNT_B64", "").strip()
        )

    @property
    def auth_configured(self) -> bool:
        return bool(self.app_username and self.app_password and self.session_secret)

    @property
    def sheets_configured(self) -> bool:
        return bool(self.gcp_service_account_b64) or self._has_separate_google_env()

    def _has_separate_google_env(self) -> bool:
        required = [
            "GOOGLE_TYPE",
            "GOOGLE_PROJECT_ID",
            "GOOGLE_PRIVATE_KEY_ID",
            "GOOGLE_PRIVATE_KEY",
            "GOOGLE_CLIENT_EMAIL",
            "GOOGLE_CLIENT_ID",
            "GOOGLE_AUTH_URI",
            "GOOGLE_TOKEN_URI",
            "GOOGLE_AUTH_PROVIDER_X509_CERT_URL",
        ]
        cert = os.getenv("GOOGLE_CLIENT_X509_CERT_URL", "") or os.getenv(
            "_CLIENT_X509_CERT_URL", ""
        )
        return all(os.getenv(key, "").strip() for key in required) and bool(cert.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
