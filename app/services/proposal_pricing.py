"""Precificação de planos Oppi para propostas comerciais."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

from app.services.legacy_core import normalize_text, parse_money

# Âncoras oficiais (boleto mensal)
ANCHOR_PLANS = (
    (10, 59.90),
    (25, 209.90),
)
RECURRING_DISCOUNT = 10.0
ANNUAL_MONTHLY_DISCOUNT = 10.0
EXTRA_COLLABORATOR = 9.90


@dataclass
class PlanPricing:
    included_collaborators: int
    boleto_monthly: float
    recorrente_monthly: float
    anual_monthly_equiv: float
    anual_upfront: float
    extra_per_collaborator: float
    cadastro_collaborators: int | None
    source_description: str
    product_label: str

    @property
    def plans_block(self) -> str:
        n = self.included_collaborators
        lines = [
            f"Plano até {n} colaboradores",
            f"Boleto: R$ {_fmt(self.boleto_monthly)}/mês",
            f"Recorrente: R$ {_fmt(self.recorrente_monthly)}/mês",
            f"Anual: R$ {_fmt(self.anual_upfront)} à vista",
            f"Equivalente a R$ {_fmt(self.anual_monthly_equiv)}/mês.",
            "",
            f"Colaboradores adicionais: R$ {_fmt(self.extra_per_collaborator)} por colaborador/mês.",
        ]
        # Bloco detalhado estilo modelo (faixa 10)
        if n <= 10:
            lines = [
                "Plano Mensal no Boleto",
                f"R$ {_fmt(self.boleto_monthly)} por mês até {n} colaboradores",
                f"Inclui acesso à plataforma para até {n} colaboradores.",
                "Plano Mensal Recorrente no Cartão",
                f"R$ {_fmt(self.recorrente_monthly)} por mês",
                f"Inclui acesso à plataforma para até {n} colaboradores.",
                "Plano Anual",
                f"R$ {_fmt(self.anual_upfront)} à vista",
                f"Equivalente a R$ {_fmt(self.anual_monthly_equiv)} por mês durante 12 meses.",
                f"Inclui acesso à plataforma para até {n} colaboradores.",
                f"Colaboradores adicionais: R$ {_fmt(self.extra_per_collaborator)} por colaborador/mês.",
            ]
        return "\n".join(lines)


def _fmt(value: float) -> str:
    return f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _round_money(value: float) -> float:
    return round(float(value) + 1e-9, 2)


def interpolate_boleto(included: int) -> float:
    """Interpola/extrapola boleto mensal a partir das âncoras 10 e 25."""
    included = max(1, int(included))
    anchors = sorted(ANCHOR_PLANS, key=lambda item: item[0])
    if included <= anchors[0][0]:
        # proporcional ao plano 10
        ratio = included / anchors[0][0]
        return _round_money(anchors[0][1] * max(ratio, 0.5))
    if included >= anchors[-1][0]:
        # extrapolação linear além de 25
        (n0, p0), (n1, p1) = anchors[-2], anchors[-1]
        slope = (p1 - p0) / (n1 - n0)
        return _round_money(p1 + slope * (included - n1))
    # entre âncoras
    for idx in range(len(anchors) - 1):
        n0, p0 = anchors[idx]
        n1, p1 = anchors[idx + 1]
        if n0 <= included <= n1:
            t = (included - n0) / (n1 - n0)
            return _round_money(p0 + t * (p1 - p0))
    return _round_money(anchors[-1][1])


def parse_collaborators_count(raw: str | int | float | None) -> int | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        value = int(raw)
        return value if value > 0 else None
    text = normalize_text(raw)
    if not text:
        return None
    # "12 colaboradores", "até 25", "10"
    match = re.search(r"(\d{1,4})", text.replace(".", ""))
    if not match:
        return None
    value = int(match.group(1))
    return value if value > 0 else None


def extract_included_from_description(description: str, fallback: int | None = None) -> int:
    text = normalize_text(description).lower()
    patterns = [
        r"at[eé]\s*(\d{1,4})\s*colabor",
        r"at[eé]\s*(\d{1,4})\s*func",
        r"(\d{1,4})\s*colabor",
        r"plano\s*(\d{1,4})",
        r"faixa\s*(\d{1,4})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return max(1, int(match.group(1)))
    if fallback and fallback > 0:
        # arredonda para cima nas âncoras conhecidas
        if fallback <= 10:
            return 10
        if fallback <= 25:
            return 25
        return int(math.ceil(fallback / 5) * 5)
    return 10


def extract_explicit_boleto(description: str) -> float | None:
    text = normalize_text(description)
    # "Boleto: R$ 209,90" ou "R$ 59,90/mês"
    match = re.search(
        r"(?:boleto[^0-9]{0,20})?R\$\s*([\d.]+,?\d*)",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    amount = parse_money(match.group(1))
    return amount if amount > 0 else None


def suggest_pricing_for_collaborators(collaborators: int) -> PlanPricing:
    """Sugere preço a partir da quantidade de colaboradores (cálculo por faixa/funcionário)."""
    n = max(1, int(collaborators))
    # Faixa comercial alinhada às âncoras
    if n <= 10:
        included = 10
    elif n <= 25:
        included = 25
    else:
        included = int(math.ceil(n / 5) * 5)
    boleto = interpolate_boleto(included)
    recorrente = _round_money(max(boleto - RECURRING_DISCOUNT, 0))
    anual_monthly = _round_money(max(boleto - ANNUAL_MONTHLY_DISCOUNT, 0))
    anual_upfront = _round_money(anual_monthly * 12)
    return PlanPricing(
        included_collaborators=included,
        boleto_monthly=boleto,
        recorrente_monthly=recorrente,
        anual_monthly_equiv=anual_monthly,
        anual_upfront=anual_upfront,
        extra_per_collaborator=EXTRA_COLLABORATOR,
        cadastro_collaborators=n,
        source_description=f"Plano até {included} colaboradores ({n} informados)",
        product_label="Ponto Eletrônico Oppi",
    )


def pricing_with_boleto_override(collaborators: int, boleto_monthly: float) -> PlanPricing:
    n = max(1, int(collaborators))
    boleto = _round_money(max(float(boleto_monthly), 0))
    recorrente = _round_money(max(boleto - RECURRING_DISCOUNT, 0))
    anual_monthly = _round_money(max(boleto - ANNUAL_MONTHLY_DISCOUNT, 0))
    anual_upfront = _round_money(anual_monthly * 12)
    included = n if n > 25 else (10 if n <= 10 else 25)
    return PlanPricing(
        included_collaborators=included,
        boleto_monthly=boleto,
        recorrente_monthly=recorrente,
        anual_monthly_equiv=anual_monthly,
        anual_upfront=anual_upfront,
        extra_per_collaborator=EXTRA_COLLABORATOR,
        cadastro_collaborators=n,
        source_description=f"Valor personalizado para {n} colaboradores",
        product_label="Ponto Eletrônico Oppi",
    )


def per_employee_rate(pricing: PlanPricing) -> float:
    base = max(1, int(pricing.cadastro_collaborators or pricing.included_collaborators or 1))
    return _round_money(pricing.boleto_monthly / base)


def format_money_br(value: float) -> str:
    return f"R$ {_fmt(value)}"


def compute_plan_pricing(
    services_description: str,
    *,
    cadastro_collaborators: str | int | None = None,
    product_label: str = "Ponto Eletrônico Oppi",
) -> PlanPricing:
    cadastro_n = parse_collaborators_count(cadastro_collaborators)
    included = extract_included_from_description(services_description, cadastro_n)
    explicit = extract_explicit_boleto(services_description)
    boleto = explicit if explicit else interpolate_boleto(included)
    recorrente = _round_money(max(boleto - RECURRING_DISCOUNT, 0))
    anual_monthly = _round_money(max(boleto - ANNUAL_MONTHLY_DISCOUNT, 0))
    anual_upfront = _round_money(anual_monthly * 12)
    return PlanPricing(
        included_collaborators=included,
        boleto_monthly=boleto,
        recorrente_monthly=recorrente,
        anual_monthly_equiv=anual_monthly,
        anual_upfront=anual_upfront,
        extra_per_collaborator=EXTRA_COLLABORATOR,
        cadastro_collaborators=cadastro_n,
        source_description=normalize_text(services_description),
        product_label=normalize_text(product_label) or "Ponto Eletrônico Oppi",
    )
