import requests

from config.settings import settings


class WhatsAppService:
    def is_configured(self) -> bool:
        return bool(settings.whatsapp_api_url and settings.whatsapp_api_token)

    def send_message(self, phone: str, message: str) -> dict:
        if not self.is_configured():
            return {"ok": False, "message": "Integração WhatsApp não configurada."}
        try:
            response = requests.post(
                settings.whatsapp_api_url,
                json={"phone": phone, "message": message},
                headers={"Authorization": f"Bearer {settings.whatsapp_api_token}"},
                timeout=20,
            )
            return {"ok": response.ok, "status_code": response.status_code, "body": response.text[:500]}
        except Exception as exc:
            return {"ok": False, "message": str(exc)}


whatsapp_service = WhatsAppService()
