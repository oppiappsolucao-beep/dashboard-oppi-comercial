import requests

from config.settings import settings


class AsaasService:
    def is_configured(self) -> bool:
        return bool(settings.asaas_api_key)

    def test_connection(self) -> dict:
        if not self.is_configured():
            return {"ok": False, "message": "ASAAS_API_KEY não configurada."}
        try:
            response = requests.get(
                f"{settings.asaas_api_url}/customers",
                headers={"access_token": settings.asaas_api_key},
                params={"limit": 1},
                timeout=20,
            )
            return {"ok": response.ok, "status_code": response.status_code}
        except Exception as exc:
            return {"ok": False, "message": str(exc)}


asaas_service = AsaasService()
