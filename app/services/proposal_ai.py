"""IA opcional para interpretar descrição de serviços na proposta."""
from __future__ import annotations

import json
import logging
import os
import re

from app.services.legacy_core import normalize_text
from app.services.proposal_pricing import PlanPricing, compute_plan_pricing

logger = logging.getLogger(__name__)


def _openai_configured() -> bool:
    return bool(os.getenv("OPENAI_API_KEY", "").strip())


def _openai_model() -> str:
    return os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"


def interpret_services_description(
    description: str,
    *,
    cadastro_collaborators: str = "",
) -> dict:
    """
    Retorna {included_collaborators, product_label, plans_text, notes}.
    Sempre tem fallback determinístico.
    """
    pricing = compute_plan_pricing(
        description,
        cadastro_collaborators=cadastro_collaborators,
    )
    result = {
        "included_collaborators": pricing.included_collaborators,
        "product_label": pricing.product_label,
        "plans_text": pricing.plans_block,
        "notes": "",
        "ai_used": False,
        "pricing": pricing,
    }
    if not _openai_configured() or not normalize_text(description):
        return result

    try:
        from openai import OpenAI

        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY", "").strip())
        system = (
            "Você auxilia propostas comerciais da Oppi (ponto eletrônico). "
            "Responda SOMENTE JSON válido com chaves: "
            "included_collaborators (int), product_label (string), "
            "plans_emphasis (string curta), rewrite_ok (bool). "
            "Regra de preço (não invente outros valores base além do contexto): "
            "boleto = valor cheio; recorrente = boleto - 10; anual mensal = boleto - 10; "
            "anual à vista = anual mensal * 12; extras = 9,90 por colaborador/mês. "
            "Âncoras: até 10 colab boleto 59,90; até 25 colab boleto 209,90; "
            "outras faixas proporcione."
        )
        user = (
            f"Descrição do atendente:\n{description}\n\n"
            f"Colaboradores no cadastro: {cadastro_collaborators or 'não informado'}\n"
            f"Cálculo interno sugerido: faixa {pricing.included_collaborators}, "
            f"boleto {pricing.boleto_monthly}, recorrente {pricing.recorrente_monthly}, "
            f"anual {pricing.anual_upfront}."
        )
        response = client.chat.completions.create(
            model=_openai_model(),
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.2,
        )
        content = response.choices[0].message.content or "{}"
        match = re.search(r"\{.*\}", content, flags=re.DOTALL)
        payload = json.loads(match.group(0) if match else content)
        included = int(payload.get("included_collaborators") or pricing.included_collaborators)
        if included > 0 and included != pricing.included_collaborators:
            pricing = compute_plan_pricing(
                f"plano até {included} colaboradores",
                cadastro_collaborators=cadastro_collaborators,
            )
        label = normalize_text(payload.get("product_label")) or pricing.product_label
        emphasis = normalize_text(payload.get("plans_emphasis"))
        plans_text = pricing.plans_block
        if emphasis:
            plans_text = f"{emphasis}\n\n{plans_text}"
        result.update(
            {
                "included_collaborators": pricing.included_collaborators,
                "product_label": label,
                "plans_text": plans_text,
                "notes": emphasis,
                "ai_used": True,
                "pricing": pricing,
            }
        )
        return result
    except Exception as error:
        logger.warning("OpenAI proposta falhou, usando fallback: %s", error)
        return result


def enrich_plans_text(pricing: PlanPricing, ai_result: dict | None = None) -> str:
    if ai_result and normalize_text(ai_result.get("plans_text")):
        return normalize_text(ai_result.get("plans_text"))
    return pricing.plans_block
