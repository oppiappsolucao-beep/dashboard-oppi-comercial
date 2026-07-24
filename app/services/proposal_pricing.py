"""Precificação única do Ponto Eletrônico Oppi (Decimal, sem float monetário)."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.services.legacy_core import normalize_text, parse_money

TWOPLACES = Decimal("0.01")

BASE_BOLETO = Decimal("59.90")
BASE_CARTAO = Decimal("49.90")
BASE_ANUAL = Decimal("468.00")
EXTRA_MENSAL = Decimal("9.90")
INCLUDED_LIMIT = 10

PLAN_BOLETO = "boleto"
PLAN_CARTAO = "cartao"
PLAN_ANUAL = "anual"

PLAN_LABELS = {
    PLAN_BOLETO: "Boleto mensal",
    PLAN_CARTAO: "Cartão recorrente",
    PLAN_ANUAL: "Anual à vista",
}

PAYMENT_LABELS = {
    PLAN_BOLETO: "boleto mensal",
    PLAN_CARTAO: "cartão recorrente",
    PLAN_ANUAL: "anual à vista",
}


def _money(value: Decimal | int | float | str) -> Decimal:
    if isinstance(value, Decimal):
        amount = value
    else:
        amount = Decimal(str(value))
    return amount.quantize(TWOPLACES, rounding=ROUND_HALF_UP)


def format_money_br(value: Decimal | float | int | str | None) -> str:
    amount = _money(value or 0)
    formatted = f"{amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {formatted}"


def parse_collaborators_count(raw: str | int | float | None) -> int | None:
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        value = int(raw)
        return value if value > 0 else None
    text = normalize_text(raw)
    if not text:
        return None
    match = re.search(r"(\d{1,4})", text.replace(".", ""))
    if not match:
        return None
    value = int(match.group(1))
    return value if value > 0 else None


@dataclass(frozen=True)
class PlanosPonto:
    quantidade_total: int
    quantidade_incluida: int
    quantidade_adicional: int
    adicional_mensal: Decimal
    valor_base_boleto: Decimal
    total_mensal_boleto: Decimal
    valor_base_cartao: Decimal
    total_mensal_cartao: Decimal
    valor_base_anual: Decimal
    adicional_anual: Decimal
    total_anual: Decimal
    mensal_equivalente_anual: Decimal
    plano_recomendado: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key, value in list(data.items()):
            if isinstance(value, Decimal):
                data[key] = str(value)
        data["plano_recomendado_label"] = PLAN_LABELS.get(self.plano_recomendado, self.plano_recomendado)
        data["labels"] = {
            "boleto": format_money_br(self.total_mensal_boleto),
            "cartao": format_money_br(self.total_mensal_cartao),
            "anual": format_money_br(self.total_anual),
            "anual_mensal": format_money_br(self.mensal_equivalente_anual),
            "adicional_mensal": format_money_br(self.adicional_mensal),
            "adicional_anual": format_money_br(self.adicional_anual),
        }
        return data

    def monthly_equivalent(self, plan_key: str) -> Decimal:
        if plan_key == PLAN_BOLETO:
            return self.total_mensal_boleto
        if plan_key == PLAN_CARTAO:
            return self.total_mensal_cartao
        return self.mensal_equivalente_anual

    def final_amount(self, plan_key: str) -> Decimal:
        if plan_key == PLAN_ANUAL:
            return self.total_anual
        if plan_key == PLAN_CARTAO:
            return self.total_mensal_cartao
        return self.total_mensal_boleto

    def suggestion_text(self) -> str:
        n = self.quantidade_total
        return (
            f"Para uma empresa com {n} colaboradores, recomendamos o pagamento anual à vista "
            f"no valor de {format_money_br(self.total_anual)}, equivalente a "
            f"{format_money_br(self.mensal_equivalente_anual)} por mês. "
            "Essa é a opção com o melhor custo-benefício entre as formas de pagamento disponíveis."
            if self.plano_recomendado == PLAN_ANUAL
            else (
                f"Para uma empresa com {n} colaboradores, recomendamos o "
                f"{PLAN_LABELS[self.plano_recomendado].lower()} no valor de "
                f"{format_money_br(self.final_amount(self.plano_recomendado))}"
                + (
                    f"/mês."
                    if self.plano_recomendado != PLAN_ANUAL
                    else f", equivalente a {format_money_br(self.mensal_equivalente_anual)} por mês."
                )
                + " Essa é a opção com o melhor custo-benefício entre as formas de pagamento disponíveis."
            )
        )


def calcular_planos_ponto(quantidade_colaboradores: int) -> PlanosPonto:
    """Única função de cálculo dos planos do Ponto Eletrônico Oppi."""
    total = int(quantidade_colaboradores)
    if total <= 0:
        raise ValueError("Informe uma quantidade de colaboradores maior que zero.")

    incluida = min(total, INCLUDED_LIMIT)
    adicional = max(total - INCLUDED_LIMIT, 0)
    adicional_mensal = _money(Decimal(adicional) * EXTRA_MENSAL)

    boleto = _money(BASE_BOLETO + adicional_mensal)
    cartao = _money(BASE_CARTAO + adicional_mensal)
    adicional_anual = _money(Decimal(adicional) * EXTRA_MENSAL * Decimal("12"))
    anual = _money(BASE_ANUAL + adicional_anual)
    mensal_anual = _money(anual / Decimal("12"))

    candidates = {
        PLAN_BOLETO: boleto,
        PLAN_CARTAO: cartao,
        PLAN_ANUAL: mensal_anual,
    }
    # Empate: preferir anual → cartão → boleto
    preferred_order = (PLAN_ANUAL, PLAN_CARTAO, PLAN_BOLETO)
    best_value = min(candidates.values())
    recomendado = next(key for key in preferred_order if candidates[key] == best_value)

    return PlanosPonto(
        quantidade_total=total,
        quantidade_incluida=incluida,
        quantidade_adicional=adicional,
        adicional_mensal=adicional_mensal,
        valor_base_boleto=BASE_BOLETO,
        total_mensal_boleto=boleto,
        valor_base_cartao=BASE_CARTAO,
        total_mensal_cartao=cartao,
        valor_base_anual=BASE_ANUAL,
        adicional_anual=adicional_anual,
        total_anual=anual,
        mensal_equivalente_anual=mensal_anual,
        plano_recomendado=recomendado,
    )


# Alias em inglês / legado
def suggest_pricing_for_collaborators(collaborators: int) -> PlanosPonto:
    return calcular_planos_ponto(collaborators)


@dataclass
class SelectedProposalPricing:
    """Snapshot da proposta atual (pode incluir override manual)."""

    planos: PlanosPonto
    plan_key: str
    payment_label: str
    plan_label: str
    valor_mensal: Decimal
    valor_anual: Decimal | None
    valor_mensal_equivalente: Decimal | None
    desconto_valor: Decimal
    desconto_percentual: Decimal
    valor_final: Decimal
    parcelas: int
    valor_parcela: Decimal | None
    observacao: str
    validade_dias: int
    manual: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "planos": self.planos.to_dict(),
            "plan_key": self.plan_key,
            "payment_label": self.payment_label,
            "plan_label": self.plan_label,
            "valor_mensal": str(self.valor_mensal),
            "valor_anual": str(self.valor_anual) if self.valor_anual is not None else "",
            "valor_mensal_equivalente": (
                str(self.valor_mensal_equivalente) if self.valor_mensal_equivalente is not None else ""
            ),
            "desconto_valor": str(self.desconto_valor),
            "desconto_percentual": str(self.desconto_percentual),
            "valor_final": str(self.valor_final),
            "parcelas": self.parcelas,
            "valor_parcela": str(self.valor_parcela) if self.valor_parcela is not None else "",
            "observacao": self.observacao,
            "validade_dias": self.validade_dias,
            "manual": self.manual,
            "valor_mensal_label": format_money_br(self.valor_mensal),
            "valor_anual_label": format_money_br(self.valor_anual) if self.valor_anual is not None else "",
            "valor_mensal_equivalente_label": (
                format_money_br(self.valor_mensal_equivalente)
                if self.valor_mensal_equivalente is not None
                else ""
            ),
            "desconto_valor_label": format_money_br(self.desconto_valor) if self.desconto_valor > 0 else "",
            "valor_final_label": format_money_br(self.valor_final),
            "valor_parcela_label": format_money_br(self.valor_parcela) if self.valor_parcela else "",
        }


def select_plan(
    planos: PlanosPonto,
    plan_key: str,
    *,
    validade_dias: int = 10,
    observacao: str = "",
) -> SelectedProposalPricing:
    key = normalize_text(plan_key).lower()
    if key in ("recorrente", "cartão", "cartao recorrente", "recorrente cartao"):
        key = PLAN_CARTAO
    elif key in ("boleto mensal", "mensal boleto"):
        key = PLAN_BOLETO
    elif key in ("anual a vista", "anual à vista", "anual"):
        key = PLAN_ANUAL
    if key not in PLAN_LABELS:
        key = planos.plano_recomendado

    if key == PLAN_ANUAL:
        valor_mensal = planos.mensal_equivalente_anual
        valor_anual = planos.total_anual
        valor_final = planos.total_anual
        equivalente = planos.mensal_equivalente_anual
    elif key == PLAN_CARTAO:
        valor_mensal = planos.total_mensal_cartao
        valor_anual = None
        valor_final = planos.total_mensal_cartao
        equivalente = None
    else:
        valor_mensal = planos.total_mensal_boleto
        valor_anual = None
        valor_final = planos.total_mensal_boleto
        equivalente = None

    return SelectedProposalPricing(
        planos=planos,
        plan_key=key,
        payment_label=PAYMENT_LABELS[key],
        plan_label=PLAN_LABELS[key],
        valor_mensal=_money(valor_mensal),
        valor_anual=_money(valor_anual) if valor_anual is not None else None,
        valor_mensal_equivalente=_money(equivalente) if equivalente is not None else None,
        desconto_valor=_money(0),
        desconto_percentual=_money(0),
        valor_final=_money(valor_final),
        parcelas=1 if key == PLAN_ANUAL else 1,
        valor_parcela=None,
        observacao=normalize_text(observacao),
        validade_dias=max(1, int(validade_dias or 10)),
        manual=False,
    )


def apply_manual_override(
    planos: PlanosPonto,
    *,
    plan_key: str,
    valor_mensal: str | Decimal | float | None = None,
    valor_anual: str | Decimal | float | None = None,
    valor_mensal_equivalente: str | Decimal | float | None = None,
    desconto_valor: str | Decimal | float | None = None,
    desconto_percentual: str | Decimal | float | None = None,
    valor_final: str | Decimal | float | None = None,
    parcelas: int | str | None = None,
    valor_parcela: str | Decimal | float | None = None,
    observacao: str = "",
    validade_dias: int | str | None = 10,
) -> SelectedProposalPricing:
    base = select_plan(planos, plan_key, validade_dias=int(validade_dias or 10), observacao=observacao)

    def _parse_optional(raw) -> Decimal | None:
        if raw in (None, ""):
            return None
        if isinstance(raw, Decimal):
            return _money(raw)
        if isinstance(raw, (int, float)):
            return _money(raw)
        text = normalize_text(str(raw))
        if not text:
            return None
        amount = parse_money(text)
        return _money(amount) if amount >= 0 else None

    mensal = _parse_optional(valor_mensal)
    anual = _parse_optional(valor_anual)
    equivalente = _parse_optional(valor_mensal_equivalente)
    desc_v = _parse_optional(desconto_valor) or _money(0)
    desc_p = _parse_optional(desconto_percentual) or _money(0)
    final = _parse_optional(valor_final)
    parcela = _parse_optional(valor_parcela)

    try:
        n_parcelas = max(1, int(parcelas or 1))
    except (TypeError, ValueError):
        n_parcelas = 1

    if mensal is not None:
        base.valor_mensal = mensal
    if anual is not None:
        base.valor_anual = anual
    if equivalente is not None:
        base.valor_mensal_equivalente = equivalente
    base.desconto_valor = desc_v
    base.desconto_percentual = desc_p
    base.parcelas = n_parcelas
    base.valor_parcela = parcela
    base.observacao = normalize_text(observacao)
    base.validade_dias = max(1, int(validade_dias or 10))
    base.manual = True

    if final is not None:
        base.valor_final = final
    else:
        # Recalcula final a partir do plano + desconto
        if base.plan_key == PLAN_ANUAL:
            bruto = base.valor_anual if base.valor_anual is not None else base.planos.total_anual
        else:
            bruto = base.valor_mensal
        if desc_p > 0:
            base.valor_final = _money(bruto * (Decimal("1") - (desc_p / Decimal("100"))))
        elif desc_v > 0:
            base.valor_final = _money(max(bruto - desc_v, Decimal("0")))
        else:
            base.valor_final = _money(bruto)

    if base.parcelas > 1 and base.valor_parcela is None:
        base.valor_parcela = _money(base.valor_final / Decimal(base.parcelas))

    return base


def plans_cards_payload(planos: PlanosPonto) -> list[dict[str, Any]]:
    return [
        {
            "key": PLAN_BOLETO,
            "title": "Boleto mensal",
            "payment": PAYMENT_LABELS[PLAN_BOLETO],
            "recommended": planos.plano_recomendado == PLAN_BOLETO,
            "quantidade_total": planos.quantidade_total,
            "quantidade_incluida": planos.quantidade_incluida,
            "quantidade_adicional": planos.quantidade_adicional,
            "valor_base": format_money_br(planos.valor_base_boleto),
            "valor_adicionais": format_money_br(planos.adicional_mensal),
            "valor_final": format_money_br(planos.total_mensal_boleto),
            "valor_anual": "",
            "valor_mensal_equivalente": "",
        },
        {
            "key": PLAN_CARTAO,
            "title": "Cartão recorrente",
            "payment": PAYMENT_LABELS[PLAN_CARTAO],
            "recommended": planos.plano_recomendado == PLAN_CARTAO,
            "quantidade_total": planos.quantidade_total,
            "quantidade_incluida": planos.quantidade_incluida,
            "quantidade_adicional": planos.quantidade_adicional,
            "valor_base": format_money_br(planos.valor_base_cartao),
            "valor_adicionais": format_money_br(planos.adicional_mensal),
            "valor_final": format_money_br(planos.total_mensal_cartao),
            "valor_anual": "",
            "valor_mensal_equivalente": "",
        },
        {
            "key": PLAN_ANUAL,
            "title": "Anual à vista",
            "payment": PAYMENT_LABELS[PLAN_ANUAL],
            "recommended": planos.plano_recomendado == PLAN_ANUAL,
            "quantidade_total": planos.quantidade_total,
            "quantidade_incluida": planos.quantidade_incluida,
            "quantidade_adicional": planos.quantidade_adicional,
            "valor_base": format_money_br(planos.valor_base_anual),
            "valor_adicionais": format_money_br(planos.adicional_anual),
            "valor_final": format_money_br(planos.total_anual),
            "valor_anual": format_money_br(planos.total_anual),
            "valor_mensal_equivalente": format_money_br(planos.mensal_equivalente_anual),
        },
    ]


# Compatibilidade com código antigo que esperava PlanPricing / plans_block
class PlanPricing:
    """Adaptador legado — preferir PlanosPonto / SelectedProposalPricing."""

    def __init__(self, planos: PlanosPonto):
        self._planos = planos
        self.included_collaborators = planos.quantidade_incluida
        self.boleto_monthly = float(planos.total_mensal_boleto)
        self.recorrente_monthly = float(planos.total_mensal_cartao)
        self.anual_monthly_equiv = float(planos.mensal_equivalente_anual)
        self.anual_upfront = float(planos.total_anual)
        self.extra_per_collaborator = float(EXTRA_MENSAL)
        self.cadastro_collaborators = planos.quantidade_total
        self.source_description = f"{planos.quantidade_total} colaboradores"
        self.product_label = "Ponto Eletrônico Oppi"

    @property
    def plans_block(self) -> str:
        p = self._planos
        lines = [
            "Plano Mensal no Boleto",
            f"Colaboradores: {p.quantidade_total}",
            f"Valor mensal: {format_money_br(p.total_mensal_boleto)}",
            f"Valor dos adicionais: {format_money_br(p.adicional_mensal)}" if p.quantidade_adicional else "",
            "Forma de pagamento: boleto mensal.",
            "",
            "Plano Mensal Recorrente no Cartão",
            f"Colaboradores: {p.quantidade_total}",
            f"Valor mensal: {format_money_br(p.total_mensal_cartao)}",
            f"Valor dos adicionais: {format_money_br(p.adicional_mensal)}" if p.quantidade_adicional else "",
            "Forma de pagamento: cartão recorrente.",
            "",
            "Plano Anual",
            f"Colaboradores: {p.quantidade_total}",
            f"Valor anual: {format_money_br(p.total_anual)}",
            f"Valor mensal equivalente: {format_money_br(p.mensal_equivalente_anual)}",
            f"Valor anual dos adicionais: {format_money_br(p.adicional_anual)}" if p.quantidade_adicional else "",
            "Forma de pagamento: anual à vista.",
            "",
            f"Colaboradores adicionais: {format_money_br(EXTRA_MENSAL)} por colaborador por mês.",
        ]
        return "\n".join(line for line in lines if line is not None)


def pricing_with_boleto_override(collaborators: int, boleto_monthly: float) -> PlanPricing:
    """Legado: override manual sobre boleto — novos fluxos usam apply_manual_override."""
    planos = calcular_planos_ponto(collaborators)
    selected = apply_manual_override(
        planos,
        plan_key=PLAN_BOLETO,
        valor_mensal=boleto_monthly,
        valor_final=boleto_monthly,
    )
    adapter = PlanPricing(planos)
    adapter.boleto_monthly = float(selected.valor_mensal)
    adapter.recorrente_monthly = float(selected.valor_mensal)
    adapter.anual_monthly_equiv = float(selected.valor_mensal)
    adapter.anual_upfront = float(_money(selected.valor_mensal * Decimal("12")))
    adapter.source_description = f"Valor personalizado para {collaborators} colaboradores"
    return adapter


def compute_plan_pricing(
    services_description: str,
    *,
    cadastro_collaborators: str | int | None = None,
    product_label: str = "Ponto Eletrônico Oppi",
) -> PlanPricing:
    count = parse_collaborators_count(cadastro_collaborators) or parse_collaborators_count(services_description) or 10
    adapter = PlanPricing(calcular_planos_ponto(count))
    adapter.product_label = normalize_text(product_label) or "Ponto Eletrônico Oppi"
    adapter.source_description = normalize_text(services_description) or adapter.source_description
    return adapter


def per_employee_rate(pricing: PlanPricing) -> float:
    base = max(1, int(pricing.cadastro_collaborators or pricing.included_collaborators or 1))
    return float(_money(Decimal(str(pricing.boleto_monthly)) / Decimal(base)))
