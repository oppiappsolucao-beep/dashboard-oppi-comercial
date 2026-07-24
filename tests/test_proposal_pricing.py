"""Testes do cálculo único de planos do Ponto Eletrônico Oppi."""
from decimal import Decimal

from app.services.proposal_pricing import calcular_planos_ponto


def _d(value: str) -> Decimal:
    return Decimal(value)


def test_planos_5_colaboradores():
    p = calcular_planos_ponto(5)
    assert p.quantidade_adicional == 0
    assert p.adicional_mensal == _d("0.00")
    assert p.total_mensal_boleto == _d("59.90")
    assert p.total_mensal_cartao == _d("49.90")
    assert p.total_anual == _d("468.00")
    assert p.mensal_equivalente_anual == _d("39.00")
    assert p.plano_recomendado == "anual"


def test_planos_10_colaboradores():
    p = calcular_planos_ponto(10)
    assert p.quantidade_adicional == 0
    assert p.total_mensal_boleto == _d("59.90")
    assert p.total_mensal_cartao == _d("49.90")
    assert p.total_anual == _d("468.00")
    assert p.mensal_equivalente_anual == _d("39.00")


def test_planos_11_colaboradores():
    p = calcular_planos_ponto(11)
    assert p.adicional_mensal == _d("9.90")
    assert p.total_mensal_boleto == _d("69.80")
    assert p.total_mensal_cartao == _d("59.80")
    assert p.adicional_anual == _d("118.80")
    assert p.total_anual == _d("586.80")
    assert p.mensal_equivalente_anual == _d("48.90")


def test_planos_15_colaboradores():
    p = calcular_planos_ponto(15)
    assert p.adicional_mensal == _d("49.50")
    assert p.total_mensal_boleto == _d("109.40")
    assert p.total_mensal_cartao == _d("99.40")
    assert p.adicional_anual == _d("594.00")
    assert p.total_anual == _d("1062.00")
    assert p.mensal_equivalente_anual == _d("88.50")


def test_planos_20_colaboradores():
    p = calcular_planos_ponto(20)
    assert p.adicional_mensal == _d("99.00")
    assert p.total_mensal_boleto == _d("158.90")
    assert p.total_mensal_cartao == _d("148.90")
    assert p.adicional_anual == _d("1188.00")
    assert p.total_anual == _d("1656.00")
    assert p.mensal_equivalente_anual == _d("138.00")
