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
        self.assertEqual(asaas_billing_type("boleto_recorrente"), "BOLETO")
        self.assertEqual(asaas_billing_type("pix_boleto"), "UNDEFINED")
        self.assertEqual(asaas_cycle("mensal"), "MONTHLY")
        self.assertEqual(asaas_cycle("anual"), "YEARLY")
        self.assertIsNone(asaas_cycle("avulso"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
