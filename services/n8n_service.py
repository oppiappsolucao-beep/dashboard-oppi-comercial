import requests

from config.settings import settings


class N8NService:
    def is_configured(self) -> bool:
        return bool(settings.n8n_webhook_url)

    def trigger(self, event: str, payload: dict) -> dict:
        if not self.is_configured():
            return {"ok": False, "message": "Webhook n8n não configurado."}
        try:
            response = requests.post(
                settings.n8n_webhook_url,
                json={"event": event, **payload},
                timeout=20,
            )
            return {"ok": response.ok, "status_code": response.status_code}
        except Exception as exc:
            return {"ok": False, "message": str(exc)}


n8n_service = N8NService()
