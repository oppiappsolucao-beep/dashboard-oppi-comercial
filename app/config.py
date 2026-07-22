import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

APP_BUILD = os.getenv("APP_BUILD", "20260722-no-quota-msg-v1").strip() or "20260722-no-quota-msg-v1"


class Settings:
    sheet_id: str = "1GAbrca0NSiJfPXaSte1qGxXCsGkQPacoRsm0PVB51gE"
    worksheet_name: str = "Folha1"
    cache_ttl_seconds: int = int(os.getenv("SHEET_CACHE_TTL_SECONDS", "300"))
    proposal_template_doc_id: str = "1iTBG1ZUMCVB-aS7QoYiC4Sym6Dgn9Z7gMN-LGLgyprI"
    proposal_pdf_folder_id: str = ""
    support_whatsapp_number: str = "5511942157917"
    support_whatsapp_label: str = "+55 11 94215-7917"

    app_username: str
    app_password: str
    session_secret: str
    gcp_service_account_b64: str = ""

    scopes: list[str] = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    def __init__(self) -> None:
        self.sheet_id = os.getenv("GOOGLE_SHEET_ID", self.sheet_id).strip() or self.sheet_id
        self.worksheet_name = (
            os.getenv("GOOGLE_WORKSHEET_NAME", self.worksheet_name).strip() or self.worksheet_name
        )
        self.app_username = os.getenv("APP_USERNAME", "").strip()
        self.app_password = os.getenv("APP_PASSWORD", "").strip()
        self.session_secret = os.getenv("SESSION_SECRET", "").strip()
        if not self.session_secret and self.app_password:
            self.session_secret = self.app_password
        self.gcp_service_account_b64 = (
            os.getenv("GCP_SERVICE_ACCOUNT_B64", "").strip()
            or os.getenv("GOOGLE_SERVICE_ACCOUNT_B64", "").strip()
        )
        cache_ttl = os.getenv("SHEET_CACHE_TTL_SECONDS", "").strip()
        if cache_ttl:
            try:
                self.cache_ttl_seconds = max(30, int(cache_ttl))
            except ValueError:
                pass
        self.proposal_template_doc_id = (
            os.getenv("PROPOSAL_TEMPLATE_DOC_ID", self.proposal_template_doc_id).strip()
            or self.proposal_template_doc_id
        )
        self.proposal_pdf_folder_id = os.getenv("PROPOSAL_PDF_FOLDER_ID", self.proposal_pdf_folder_id).strip()
        self.support_whatsapp_number = (
            os.getenv("SUPPORT_WHATSAPP_NUMBER", self.support_whatsapp_number).strip()
            or self.support_whatsapp_number
        )
        self.support_whatsapp_label = (
            os.getenv("SUPPORT_WHATSAPP_LABEL", self.support_whatsapp_label).strip()
            or self.support_whatsapp_label
        )

    @property
    def support_whatsapp_url(self) -> str:
        digits = "".join(ch for ch in self.support_whatsapp_number if ch.isdigit())
        return f"https://wa.me/{digits}"

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
