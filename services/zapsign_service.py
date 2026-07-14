import requests

from config.settings import settings


class ZapSignService:
    def is_configured(self) -> bool:
        return bool(settings.zapsign_api_token)

    def test_connection(self) -> dict:
        if not self.is_configured():
            return {"ok": False, "message": "ZAPSIGN_API_TOKEN não configurado."}
        try:
            response = requests.get(
                f"{settings.zapsign_api_url}/docs/",
                headers={"Authorization": f"Bearer {settings.zapsign_api_token}"},
                timeout=20,
            )
            return {"ok": response.ok, "status_code": response.status_code}
        except Exception as exc:
            return {"ok": False, "message": str(exc)}


zapsign_service = ZapSignService()
