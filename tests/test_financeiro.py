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

    def test_month_entries_include_boleto_pix_and_card(self):
        from app.services.financeiro import charges_entered_in_period

        rows = [
            {"billing_type": "BOLETO", "status_key": "pago", "entrada": date(2026, 10, 2), "valor": 100},
            {"billing_type": "PIX", "status_key": "receber", "entrada": date(2026, 10, 8), "valor": 50},
            {"billing_type": "CREDIT_CARD", "status_key": "pago", "entrada": date(2026, 10, 9), "valor": 80},
            {"billing_type": "DEBIT_CARD", "status_key": "pago", "entrada": date(2026, 9, 30), "valor": 10},
            {"billing_type": "BOLETO", "status_key": "cancelado", "entrada": date(2026, 10, 3), "valor": 999},
            {"billing_type": "TRANSFER", "status_key": "pago", "entrada": date(2026, 10, 4), "valor": 20},
        ]
        entered = charges_entered_in_period(rows, date(2026, 10, 1), date(2026, 10, 31))
        self.assertEqual([row["billing_type"] for row in entered], ["CREDIT_CARD", "PIX", "BOLETO"])
        self.assertEqual(sum(row["valor"] for row in entered), 230)


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


class BoletosEntradaTest(unittest.TestCase):
    def test_conta_hoje_e_mes_sem_cancelado(self):
        from app.services.financeiro import summarize_boletos

        today = date(2026, 10, 9)
        hoje = [
            {"billingType": "BOLETO", "status": "PENDING", "value": 59.9, "dueDate": "2026-10-09", "description": "Oppi RH — Alfa"},
            {"billingType": "BOLETO", "status": "DELETED", "value": 10, "dueDate": "2026-10-09", "description": "Cancelado"},
            {"billingType": "PIX", "status": "PENDING", "value": 20, "dueDate": "2026-10-09", "description": "Pix"},
        ]
        mes = hoje + [
            {"billingType": "BOLETO", "status": "RECEIVED", "value": 49.9, "dueDate": "2026-10-03", "description": "Oppi RH — Beta"},
            {"billingType": "BOLETO", "status": "OVERDUE", "value": 119.8, "dueDate": "2026-10-01", "description": "Oppi RH — Gama"},
        ]
        out = summarize_boletos(hoje, mes, today)
        self.assertEqual(out["hoje_n"], 1)
        self.assertEqual(out["hoje_valor_label"], "R$ 59,90")
        self.assertEqual(out["mes_n"], 3)
        self.assertEqual(out["mes_pagos_n"], 1)
        self.assertEqual(out["mes_abertos_n"], 2)
        self.assertEqual(out["month_label"], "Outubro 2026")

    def test_boletos_que_entraram_usam_data_de_pagamento(self):
        from app.services.financeiro import received_entries

        rows = received_entries(
            [
                {"id": "1", "billingType": "BOLETO", "status": "RECEIVED", "value": 59.9, "paymentDate": "2026-10-04", "dueDate": "2026-09-10", "description": "Oppi RH — Alfa"},
                {"id": "2", "billingType": "PIX", "status": "RECEIVED", "value": 100, "paymentDate": "2026-10-05", "description": "Pix avulso"},
                {"id": "3", "billingType": "BOLETO", "status": "RECEIVED", "value": 49.9, "paymentDate": "2026-09-28", "description": "Mês anterior"},
                {"id": "4", "billingType": "BOLETO", "status": "PENDING", "value": 10, "dueDate": "2026-10-09", "description": "Ainda aberto"},
            ],
            date(2026, 10, 1),
            date(2026, 10, 31),
            date(2026, 10, 9),
        )
        boletos = [row for row in rows if row["billing_type"] == "BOLETO"]
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(boletos), 1)
        self.assertEqual(boletos[0]["cliente"], "Oppi RH — Alfa")
        self.assertEqual(boletos[0]["pago_label"], "04/10/2026")


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

    def test_status_e_repeticao_mensal(self):
        from app.services.company_payables import add_months, payable_status

        hoje = date(2026, 10, 9)
        self.assertEqual(payable_status(date(2026, 10, 1), False, hoje), "atrasado")
        self.assertEqual(payable_status(date(2026, 10, 9), False, hoje), "a_pagar")
        self.assertEqual(payable_status(date(2026, 10, 1), True, hoje), "pago")
        self.assertEqual(add_months(date(2026, 1, 31), 1), date(2026, 2, 28))
        self.assertEqual(add_months(date(2026, 10, 9), 3), date(2027, 1, 9))


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
