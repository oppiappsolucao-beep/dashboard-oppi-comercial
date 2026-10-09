"""Testes locais da aba Financeiro (sem chamar a API Asaas)."""
from __future__ import annotations

import unittest
from datetime import date

from app.services.financeiro import billing_label, classify_payment, cycle_label, format_brl


class FinanceiroMappingTest(unittest.TestCase):
    def test_paid_is_green(self):
        out = classify_payment({"status": "RECEIVED", "dueDate": "2026-08-01"}, today=date(2026, 8, 14))
        self.assertEqual(out["key"], "pago")
        self.assertEqual(out["tone"], "green")

    def test_overdue_is_red(self):
        out = classify_payment({"status": "OVERDUE", "dueDate": "2026-08-01"}, today=date(2026, 8, 14))
        self.assertEqual(out["key"], "atrasado")
        self.assertEqual(out["tone"], "red")

    def test_due_today_is_yellow(self):
        out = classify_payment({"status": "PENDING", "dueDate": "2026-08-14"}, today=date(2026, 8, 14))
        self.assertEqual(out["key"], "vence_hoje")
        self.assertEqual(out["tone"], "yellow")

    def test_pending_future_is_blue(self):
        out = classify_payment({"status": "PENDING", "dueDate": "2026-08-20"}, today=date(2026, 8, 14))
        self.assertEqual(out["key"], "receber")
        self.assertEqual(out["tone"], "blue")

    def test_subscription_pending_is_purple(self):
        out = classify_payment(
            {"status": "PENDING", "dueDate": "2026-08-20", "subscription": "sub_1"},
            today=date(2026, 8, 14),
        )
        self.assertEqual(out["key"], "recorrente")
        self.assertEqual(out["tone"], "purple")

    def test_labels(self):
        self.assertEqual(format_brl(199), "R$ 199,00")
        self.assertEqual(billing_label("BOLETO"), "Boleto")
        self.assertEqual(billing_label("CREDIT_CARD", has_subscription=True), "Cartão recorrente")
        self.assertEqual(cycle_label("MONTHLY"), "Mensal")

    def test_service_name_strips_repeated_client(self):
        from app.services.financeiro import _service_name

        self.assertEqual(
            _service_name({"description": "Oppi RH — ITALO KLENYS"}, "ITALO KLENYS"),
            "Oppi RH",
        )
        self.assertEqual(_service_name({"description": "Parcela 2 de 12"}, "MARCOS"), "Parcela 2 de 12")


class CadastroBillingMappingTest(unittest.TestCase):
    def test_asaas_types(self):
        from app.services.cadastro_billing import asaas_billing_type, asaas_cycle

        self.assertEqual(asaas_billing_type("pix"), "PIX")
        self.assertEqual(asaas_billing_type("cartao_recorrente"), "CREDIT_CARD")
        self.assertEqual(asaas_billing_type("cartao_avulso"), "CREDIT_CARD")
        self.assertEqual(asaas_billing_type("boleto_recorrente"), "BOLETO")
        self.assertEqual(asaas_billing_type("pix_boleto"), "UNDEFINED")
        self.assertEqual(asaas_cycle("mensal"), "MONTHLY")
        self.assertEqual(asaas_cycle("anual"), "YEARLY")
        self.assertIsNone(asaas_cycle("avulso"))

    def test_payment_option_order_and_labels(self):
        from app.services.cadastro_billing import BILLING_FORM_OPTIONS, forma_label, is_recurring_forma

        self.assertEqual(
            [key for key, _ in BILLING_FORM_OPTIONS],
            ["cartao_recorrente", "boleto_recorrente", "cartao_avulso", "pix_boleto"],
        )
        self.assertEqual(
            [label for _, label in BILLING_FORM_OPTIONS],
            [
                "Cartão recorrente",
                "Boleto recorrente",
                "Cartão de crédito (Avulso)",
                "Pix/Boleto (Avulso)",
            ],
        )
        self.assertTrue(is_recurring_forma("cartao_recorrente"))
        self.assertFalse(is_recurring_forma("cartao_avulso"))
        self.assertFalse(is_recurring_forma("pix_boleto"))

    def test_parse_billing_plan_from_form_keeps_selected_option(self):
        from app.services.cadastro_billing import parse_billing_plan_from_form

        plan = parse_billing_plan_from_form(
            {
                "billing_ciclo": "mensal",
                "billing_forma": "boleto_recorrente",
                "billing_servico": "Oppi RH",
                "billing_valor": "R$ 59,90",
            }
        )
        self.assertEqual(plan["ciclo"], "mensal")
        self.assertEqual(plan["forma"], "boleto_recorrente")
        self.assertEqual(plan["servico"], "Oppi RH")
        self.assertEqual(plan["valor"], "R$ 59,90")


class EntradasEContasTest(unittest.TestCase):
    def test_extrato_ignora_tarifa_e_saida(self):
        from app.services.financeiro import map_entradas

        rows = map_entradas(
            [
                {
                    "id": "ft_1",
                    "type": "PAYMENT_RECEIVED",
                    "value": 59.9,
                    "date": "2026-10-07",
                    "paymentId": "pay_1",
                },
                {"id": "ft_2", "type": "PAYMENT_FEE", "value": -2.99, "date": "2026-10-07"},
                {"id": "ft_3", "type": "TRANSFER", "value": -100, "date": "2026-10-08"},
                {"id": "ft_4", "type": "PIX_TRANSACTION_CREDIT", "value": 120, "date": "2026-10-15"},
            ],
            {"pay_1": {"cliente": "Cliente A", "servico": "Oppi RH"}},
        )
        self.assertEqual([row["id"] for row in rows], ["ft_4", "ft_1"])
        self.assertEqual(rows[1]["description"], "Cliente A — Oppi RH")
        self.assertEqual(rows[1]["tipo"], "Cobrança recebida")
        self.assertEqual(rows[0]["tipo"], "Pix recebido")
        self.assertAlmostEqual(sum(row["valor"] for row in rows), 179.9)

    def test_grade_separa_a_pagar_e_pago(self):
        from app.services.company_payables import payable_calendar, parse_money

        self.assertEqual(parse_money("1.870,20"), 1870.2)
        self.assertIsNone(parse_money("0"))
        rows = [
            {"due": date(2026, 10, 7), "amount": 6.0, "paid": False, "description": "Taxa"},
            {"due": date(2026, 10, 15), "amount": 1540.2, "paid": True, "description": "Folha"},
        ]
        calendar = payable_calendar(rows, date(2026, 10, 1), date(2026, 10, 31))
        self.assertTrue(calendar["show_grid"])
        self.assertEqual(len(calendar["days"]), 31)
        self.assertAlmostEqual(calendar["a_pagar"], 6.0)
        self.assertAlmostEqual(calendar["pago"], 1540.2)
        self.assertEqual(calendar["days"][6]["day"], 7)
        self.assertAlmostEqual(calendar["days"][6]["a_pagar"], 6.0)
        self.assertTrue(calendar["days"][0]["empty"])


class InternalFinanceTest(unittest.TestCase):
    def test_monthly_occurrences_in_august(self):
        from app.services.internal_finance import occurrences_in_period
        from datetime import date

        item = {
            "vencimento": "2026-01-10",
            "forma_pagamento": "Mensal",
            "valor": "49,90",
            "quantidade": "1",
        }
        dates = occurrences_in_period(item, date(2026, 8, 1), date(2026, 8, 31))
        self.assertEqual(dates, [date(2026, 8, 10)])

    def test_catalog_accepts_legacy_string_list(self):
        from app.services.commercial_services import _normalize_catalog

        items = _normalize_catalog(["Oppi RH", {"name": "Oppi Ponto", "valor": "49,90", "quantidade": 2}])
        self.assertEqual(items[0]["name"], "Oppi RH")
        self.assertEqual(items[1]["quantidade"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
