from config.settings import settings


class AIService:
    def is_configured(self) -> bool:
        return bool(settings.openai_api_key)

    def parse_proposal_request(self, prompt: str, companies: list[dict], services: list[dict]) -> dict:
        if not self.is_configured():
            return {
                "configured": False,
                "message": "IA não configurada. Use o formulário tradicional ou defina OPENAI_API_KEY.",
            }

        try:
            from openai import OpenAI

            client = OpenAI(api_key=settings.openai_api_key)
            services_text = ", ".join(f"{s['name']} (R$ {s['unit_value']})" for s in services[:20])
            companies_text = ", ".join(c["company_name"] for c in companies[:20])
            system = (
                "Você extrai dados de propostas comerciais em JSON. "
                "Campos: company_name, title, services (lista), total_value, payment_terms, validity_days, notes."
            )
            user_msg = f"Empresas: {companies_text}\nServiços: {services_text}\nPedido: {prompt}"
            response = client.chat.completions.create(
                model=settings.openai_model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_msg},
                ],
                temperature=0.2,
            )
            content = response.choices[0].message.content or "{}"
            return {"configured": True, "raw": content, "prompt": prompt}
        except Exception as exc:
            return {"configured": True, "error": str(exc), "prompt": prompt}


ai_service = AIService()
