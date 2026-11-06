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

    def test_asaas_usa_email_de_cobranca_e_ignora_contador(self):
        from app.services.cadastro_billing import billing_email_from_values

        email = billing_email_from_values({
            "email_empresa": "contador@escritorio.com",
            "email": "contador@escritorio.com",
            "email_socio_1": "contador@escritorio.com",
            "email_login_gestor": "dono@acme.com",
            "email_cobranca": "financeiro@acme.com",
        })
        self.assertEqual(email, "financeiro@acme.com")

    def test_ponto_nao_leva_email_da_receita(self):
        from app.services.oppi_ponto_bridge import build_onboard_payload_from_crm

        payload = build_onboard_payload_from_crm(
            values={
                "empresa": "ACME LTDA",
                "cnpj": "18.088.253/0001-24",
                "email": "contador@escritorio.com",
                "email_empresa": "contador@escritorio.com",
                "email_socio_1": "contador@escritorio.com",
                "socio_1": "SOCIO DA RECEITA",
                "responsavel_legal": "Maria Dona",
                "telefone_b2b": "11988887777",
            },
            access={
                "email_login_gestor": "contador@escritorio.com",
                "email_cobranca": "financeiro@acme.com",
                "email_confirmacao_admin": "contador@escritorio.com",
                "senha_acesso": "Ab12cd34",
            },
        )
        self.assertEqual(payload["admin_nome"], "Maria Dona")
        self.assertEqual(payload["email_login"], "financeiro@acme.com")
        self.assertEqual(payload["email_cobranca"], "financeiro@acme.com")
        self.assertEqual(payload["email_verificacao"], "financeiro@acme.com")
        self.assertNotEqual(payload["email_login"], "contador@escritorio.com")

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
            {"billingType": "UNDEFINED", "status": "PENDING", "value": 80, "dueDate": "2026-10-09", "description": "Oppi RH — Delta"},
        ]
        mes = hoje + [
            {"billingType": "BOLETO", "status": "RECEIVED", "value": 49.9, "dueDate": "2026-10-03", "description": "Oppi RH — Beta"},
            {"billingType": "BOLETO", "status": "OVERDUE", "value": 119.8, "dueDate": "2026-10-01", "description": "Oppi RH — Gama"},
        ]
        out = summarize_boletos(hoje, mes, today)
        self.assertEqual(out["hoje_n"], 2)
        self.assertEqual(out["hoje_valor_label"], "R$ 139,90")
        self.assertEqual(out["mes_n"], 4)
        self.assertEqual(out["mes_pagos_n"], 1)
        self.assertEqual(out["mes_abertos_n"], 3)
        self.assertEqual(out["month_label"], "Outubro 2026")

    def test_vencimento_no_dia_ignora_o_dia_seguinte(self):
        from app.services.financeiro import _payments_due_on

        rows = _payments_due_on(
            [
                {"id": "hoje", "dueDate": "2026-10-10"},
                {"id": "amanha", "dueDate": "2026-10-11"},
            ],
            date(2026, 10, 10),
            date(2026, 10, 10),
        )
        self.assertEqual([row["id"] for row in rows], ["hoje"])

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
        outras = [row for row in rows if row["billing_type"] != "BOLETO"]
        self.assertEqual(len(outras), 1)
        self.assertEqual(outras[0]["cliente"], "Pix avulso")
        self.assertEqual(outras[0]["forma"], "PIX")
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

    def test_contas_a_pagar_separa_por_semana(self):
        from app.services.financeiro import _apply_week_choice, _group_payable_weeks

        listed = [
            {"due": date(2026, 11, 3), "amount": 10.0, "paid": False, "description": "Luz", "due_label": "03/11/2026"},
            {"due": date(2026, 11, 10), "amount": 40.0, "paid": True, "description": "Aluguel", "due_label": "10/11/2026"},
            {"due": date(2026, 11, 30), "amount": 5.0, "paid": False, "description": "Internet", "due_label": "30/11/2026"},
        ]
        today = date(2026, 11, 6)
        start, end = date(2026, 11, 1), date(2026, 11, 30)
        semanas = _group_payable_weeks(listed, today, start, end)
        nomes = {semana["indice"]: [item["description"] for item in semana["linhas"]] for semana in semanas}
        self.assertEqual(nomes[1], ["Luz"])
        self.assertEqual(nomes[2], ["Aluguel"])
        self.assertEqual(nomes[5], ["Internet"])
        self.assertAlmostEqual(next(item["a_pagar"] for item in semanas if item["indice"] == 1), 10.0)
        self.assertAlmostEqual(next(item["pago"] for item in semanas if item["indice"] == 2), 40.0)
        self.assertTrue(next(item["atual"] for item in semanas if item["indice"] == 1))

        view = {"semanas": list(semanas)}
        params = {}
        _apply_week_choice(view, params, start, end, today)
        self.assertEqual(params["semana"], "1")
        self.assertEqual([item["description"] for semana in view["semanas"] for item in semana["linhas"]], ["Luz"])
        self.assertEqual(view["semanas_opcoes"][0]["label"], "Mês inteiro")

        inteiro = {"semanas": list(semanas)}
        _apply_week_choice(inteiro, {"semana": "0"}, start, end, today)
        self.assertEqual(len(inteiro["semanas"]), 3)

    def test_grafico_compara_entrada_e_saida_por_semana(self):
        from app.services.financeiro import _montar_grafico_caixa

        grafico = _montar_grafico_caixa(
            {(2026, 11, 0): 10.0, (2026, 11, 1): 30.0},
            [{"ano": 2026, "mes": 11, "indice": 0, "valor": 40.0}],
            date(2026, 11, 1),
            date(2026, 11, 30),
        )
        self.assertEqual([ponto["label"] for ponto in grafico["pontos"]], ["1–7", "8–14", "15–21", "22–28", "29–31"])
        self.assertEqual([ponto["entrada"] for ponto in grafico["pontos"]], [10.0, 30.0, 0.0, 0.0, 0.0])
        self.assertEqual(grafico["pontos"][0]["saida"], 40.0)
        self.assertIn(",", grafico["entrada_pontos"])
        self.assertIn(",", grafico["saida_pontos"])

    def test_status_e_repeticao_mensal(self):
        from app.services.company_payables import add_months, payable_status

        hoje = date(2026, 10, 9)
        self.assertEqual(payable_status(date(2026, 10, 1), False, hoje), "atrasado")
        self.assertEqual(payable_status(date(2026, 10, 9), False, hoje), "a_pagar")
        self.assertEqual(payable_status(date(2026, 10, 1), True, hoje), "pago")
        self.assertEqual(add_months(date(2026, 1, 31), 1), date(2026, 2, 28))
        self.assertEqual(add_months(date(2026, 10, 9), 3), date(2027, 1, 9))

    def test_entrada_soma_o_que_foi_marcado_como_pago(self):
        from app.services.financeiro import entrada_status_key, soma_marcado_ja_pagou

        pago = {
            "empresa": "Pago",
            "plano": "Oppi RH",
            "cnpj": "",
            "linha": 4,
            "filial": False,
            "fechamento": date(2026, 8, 10),
            "valor": 50.0,
            "status": "",
            "situacao": "Ativo",
            "anual": False,
        }
        aberto = {**pago, "empresa": "Aberto", "linha": 5, "valor": 20.0}
        chave_nov = entrada_status_key(4, date(2026, 11, 10), "Pago", "Oppi RH")
        chave_dez = entrada_status_key(4, date(2026, 12, 10), "Pago", "Oppi RH")
        marcado = {chave_nov: "ja_pagou", chave_dez: "ja_pagou"}
        novembro = soma_marcado_ja_pagou(
            [pago, aberto],
            start=date(2026, 11, 1),
            end=date(2026, 11, 30),
            overrides=marcado,
        )
        dois_meses = soma_marcado_ja_pagou(
            [pago, aberto],
            start=date(2026, 11, 1),
            end=date(2026, 12, 31),
            overrides=marcado,
        )
        self.assertAlmostEqual(novembro, 50.0)
        self.assertAlmostEqual(dois_meses, 100.0)
        from app.services.financeiro import soma_vai_pagar

        a_receber = soma_vai_pagar(
            [pago, aberto],
            start=date(2026, 11, 1),
            end=date(2026, 11, 30),
            today=date(2026, 11, 6),
            overrides=marcado,
        )
        self.assertAlmostEqual(a_receber, 20.0)

    def test_reajuste_vale_no_mes_ou_em_todos(self):
        import os
        import tempfile
        from unittest.mock import patch

        import app.services.crm_local_db as local_db
        from app.services.company_payables import create_payable, list_payables, update_payable

        previous = os.environ.get("APP_STORAGE_DIR")
        was_ready = local_db._initialized
        os.environ["APP_STORAGE_DIR"] = tempfile.mkdtemp()
        local_db._initialized = False
        try:
            with patch("app.services.company_payables._uses_postgres", return_value=False):
                create_payable("Aluguel", 1000, date(2026, 11, 10), repeat_count=3)
                rows = list_payables(date(2026, 11, 1), date(2027, 1, 31))
                self.assertEqual(len(rows), 3)
                self.assertTrue(rows[0]["repete"])
                update_payable(
                    rows[0]["id"],
                    description="Aluguel",
                    amount=1100,
                    due=date(2026, 11, 10),
                    category="fixa",
                    scope="mes",
                )
                after_month = list_payables(date(2026, 11, 1), date(2027, 1, 31))
                self.assertEqual([row["amount"] for row in after_month], [1100.0, 1000.0, 1000.0])
                update_payable(
                    after_month[1]["id"],
                    description="Aluguel reajustado",
                    amount=1200,
                    due=date(2026, 12, 15),
                    category="fixa",
                    scope="todos",
                )
                after_all = list_payables(date(2026, 11, 1), date(2027, 1, 31))
        finally:
            if previous is None:
                os.environ.pop("APP_STORAGE_DIR", None)
            else:
                os.environ["APP_STORAGE_DIR"] = previous
            local_db._initialized = was_ready
        self.assertEqual([row["amount"] for row in after_all], [1200.0, 1200.0, 1200.0])
        self.assertEqual([row["description"] for row in after_all], ["Aluguel reajustado"] * 3)
        self.assertEqual(
            [row["due"] for row in after_all],
            [date(2026, 11, 15), date(2026, 12, 15), date(2027, 1, 15)],
        )


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


class RaissaEntradaTest(unittest.TestCase):
    def _sheet(self):
        return {
            "aba": "Raissa",
            "aviso": "",
            "colunas": ["Empresa", "CNPJ", "Plano", "Valor", "Data de vencimento", "Status", "Forma de pagamento"],
            "clientes": [
                {
                    "Empresa": "Alfa",
                    "CNPJ": "",
                    "Plano": "Oppi RH",
                    "Valor": "R$ 59,90",
                    "Data de vencimento": "10/11/2026",
                    "Status": "1 Boleto Aguardando",
                    "Forma de pagamento": "Boleto recorrente",
                },
                {
                    "Empresa": "Beta",
                    "CNPJ": "",
                    "Plano": "Oppi RH",
                    "Valor": "49,90",
                    "Data de vencimento": "03/11/2026",
                    "Status": "Pago",
                    "Forma de pagamento": "PIX",
                },
                {
                    "Empresa": "Gama",
                    "CNPJ": "",
                    "Plano": "Oppi Ponto",
                    "Valor": "119,80",
                    "Data de vencimento": "01/11/2026",
                    "Status": "Vencido",
                    "Forma de pagamento": "Boleto",
                },
                {
                    "Empresa": "Delta",
                    "CNPJ": "",
                    "Plano": "Oppi Ponto",
                    "Valor": "80,00",
                    "Data de vencimento": "02/10/2026",
                    "Status": "Pago",
                    "Forma de pagamento": "Cartão",
                },
                {
                    "Empresa": "Eco",
                    "CNPJ": "",
                    "Plano": "Oppi RH",
                    "Valor": "59,90",
                    "Data de vencimento": "dia 15",
                    "Status": "Aguardando",
                    "Forma de pagamento": "Boleto",
                },
                {
                    "Empresa": "Fora",
                    "CNPJ": "",
                    "Plano": "Oppi RH",
                    "Valor": "10,00",
                    "Data de vencimento": "10/11/2026",
                    "Status": "Cancelado",
                    "Forma de pagamento": "",
                },
            ],
        }

    def test_resumo_separa_a_entrar_pago_vencido_e_plano(self):
        from app.services.financeiro import raissa_rows_from_sheet, summarize_raissa_entrada

        today = date(2026, 11, 6)
        rows = raissa_rows_from_sheet(self._sheet(), today=today)
        out = summarize_raissa_entrada(
            rows,
            start=date(2026, 11, 1),
            end=date(2026, 11, 30),
            today=today,
        )
        self.assertEqual(out["pendente_n"], 3)
        self.assertEqual(out["ja_pagou_n"], 1)
        self.assertEqual(out["vai_pagar_n"], 0)
        self.assertEqual(out["ativos_n"], 5)
        self.assertEqual(out["cancelados_n"], 1)
        self.assertAlmostEqual(out["ja_pagou_label"] and 49.9, 49.9)
        precos = {row["label"]: row for row in out["valores"]}
        self.assertEqual(precos["R$ 59,90"]["n"], 2)
        self.assertEqual(precos["R$ 59,90"]["ativos"], 2)
        meses = {row["label"]: row for row in out["meses"]}
        self.assertEqual(meses["Novembro 2026"]["n"], 3)
        self.assertEqual(meses["Outubro 2026"]["n"], 1)
        self.assertTrue(meses["Novembro 2026"]["selected"])
        self.assertIn("Todo mês (dia fixo)", meses)
        self.assertEqual([row["cliente"] for row in out["linhas"]], ["Gama", "Alfa", "Eco", "Beta"])

    def test_valor_do_servico_vence_valor_generico_e_separa_ativo(self):
        from app.services.financeiro import raissa_rows_from_sheet, summarize_raissa_entrada

        headers = [""] * 36
        headers[0] = "Empresa"
        headers[2] = "Serviço"
        headers[5] = "Valor"
        headers[13] = "Status"
        headers[31] = "Serviço 2"
        headers[32] = "Valor do serviço"
        headers[34] = "AI"
        headers[35] = "Data"
        sheet = {
            "aba": "Raissa",
            "colunas": headers,
            "clientes": [
                {
                    "Empresa": "Alfa",
                    "Serviço": "",
                    "Valor": "R$ 999,00",
                    "Status": "1 Boleto Aguardando",
                    "Serviço 2": "Oppi RH",
                    "Valor do serviço": "R$ 59,90",
                    "AI": "Ativo",
                    "Data": "10/11/2026",
                },
                {
                    "Empresa": "Beta",
                    "Serviço": "",
                    "Valor": "R$ 10,00",
                    "Status": "Pago",
                    "Serviço 2": "Oppi Ponto",
                    "Valor do serviço": "R$ 59,90",
                    "AI": "Inativo",
                    "Data": "03/11/2026",
                },
            ],
        }
        today = date(2026, 11, 6)
        rows = raissa_rows_from_sheet(sheet, today=today)
        self.assertEqual(rows[0]["valor"], 59.9)
        self.assertEqual(rows[0]["plano"], "Oppi RH")
        out = summarize_raissa_entrada(rows, start=date(2026, 11, 1), end=date(2026, 11, 30), today=today)
        self.assertEqual(out["ativos_n"], 1)
        self.assertEqual(out["cancelados_n"], 1)
        self.assertEqual(out["valores"][0]["label"], "R$ 59,90")
        self.assertEqual(out["valores"][0]["n"], 2)
        self.assertEqual(out["valores"][0]["ativos"], 1)
        self.assertEqual(out["valores"][0]["cancelados"], 1)
        self.assertEqual([row["cliente"] for row in out["linhas"]], ["Alfa"])
        self.assertEqual(out["linhas"][0]["status_key"], "pendente")

    def test_coluna_ai_inativo_conta_como_cancelado(self):
        from app.services.financeiro import raissa_rows_from_sheet, summarize_raissa_entrada

        headers = [f"Coluna {index}" for index in range(35)]
        headers[0] = "Empresa"
        headers[3] = "Fechamento"
        headers[4] = "Valor do serviço"
        headers[10] = "Ativo"
        headers[34] = "Observação interna"
        sheet = {
            "aba": "Raissa",
            "colunas": headers,
            "clientes": [
                {
                    "Empresa": "Viva",
                    "Fechamento": "10/11/2026",
                    "Valor do serviço": "59,90",
                    "Ativo": "Ativo",
                    "Observação interna": "Ativo",
                },
                {
                    "Empresa": "Parou",
                    "Fechamento": "10/11/2026",
                    "Valor do serviço": "59,90",
                    "Ativo": "Ativo",
                    "Observação interna": "Inativo",
                },
            ],
        }
        today = date(2026, 11, 6)
        rows = raissa_rows_from_sheet(sheet, today=today)
        self.assertEqual(rows[1]["situacao"], "Inativo")
        out = summarize_raissa_entrada(rows, start=date(2026, 11, 1), end=date(2026, 11, 30), today=today)
        self.assertEqual(out["ativos_n"], 1)
        self.assertEqual(out["cancelados_n"], 1)
        self.assertEqual([row["cliente"] for row in out["linhas"]], ["Viva"])

    def test_coluna_d_projeta_todo_mes_e_anual_so_no_aniversario(self):
        from app.services.financeiro import raissa_rows_from_sheet, summarize_raissa_entrada

        sheet = {
            "aba": "Raissa",
            "colunas": ["Empresa", "CNPJ", "Plano", "Fechamento", "Valor do serviço", "Status", "AI"],
            "clientes": [
                {
                    "Empresa": "Mensal",
                    "CNPJ": "",
                    "Plano": "Oppi RH",
                    "Fechamento": "10/08/2026",
                    "Valor do serviço": "R$ 59,90",
                    "Status": "Pago",
                    "AI": "Ativo",
                },
                {
                    "Empresa": "Anual",
                    "CNPJ": "",
                    "Plano": "Oppi Anual",
                    "Fechamento": "10/03/2026",
                    "Valor do serviço": "R$ 200,00",
                    "Status": "",
                    "AI": "Ativo",
                },
                {
                    "Empresa": "Futuro",
                    "CNPJ": "",
                    "Plano": "Oppi RH",
                    "Fechamento": "20/12/2026",
                    "Valor do serviço": "R$ 59,90",
                    "Status": "",
                    "AI": "Ativo",
                },
                {
                    "Empresa": "Parou",
                    "CNPJ": "",
                    "Plano": "Oppi RH",
                    "Fechamento": "05/01/2026",
                    "Valor do serviço": "R$ 59,90",
                    "Status": "",
                    "AI": "Inativo",
                },
            ],
        }
        today = date(2026, 11, 6)
        rows = raissa_rows_from_sheet(sheet, today=today)
        self.assertEqual(rows[0]["fechamento"], date(2026, 8, 10))
        self.assertFalse(rows[0]["anual"])
        self.assertTrue(rows[1]["anual"])
        novembro = summarize_raissa_entrada(rows, start=date(2026, 11, 1), end=date(2026, 11, 30), today=today)
        self.assertEqual([row["cliente"] for row in novembro["linhas"]], ["Mensal"])
        self.assertEqual(novembro["linhas"][0]["vencimento_label"], "10/11/2026")
        self.assertEqual(novembro["linhas"][0]["status_key"], "ja_pagou")
        meses = {row["label"]: row for row in novembro["meses"]}
        self.assertEqual(meses["Novembro 2026"]["pago_n"], 1)
        self.assertEqual(meses["Dezembro 2026"]["vai_n"], 2)
        pizzas = {pizza["titulo"]: pizza for pizza in novembro["pizzas"]}
        fatias_2026 = {fatia["mes"]: fatia["valor"] for fatia in pizzas["2026 · Recorrente"]["fatias"]}
        fatias_2027 = {fatia["mes"]: fatia["valor"] for fatia in pizzas["2027 · Recorrente"]["fatias"]}
        anuais_2027 = {fatia["mes"]: fatia["valor"] for fatia in pizzas["2027 · Anuais"]["fatias"]}
        self.assertNotIn("2026 · Anuais", pizzas)
        self.assertEqual(fatias_2026, {"Novembro": 59.9, "Dezembro": 119.8})
        self.assertEqual(fatias_2027["Janeiro"], 119.8)
        self.assertEqual(fatias_2027["Março"], 119.8)
        self.assertEqual(fatias_2027["Dezembro"], 119.8)
        self.assertEqual(len(fatias_2027), 12)
        self.assertEqual(anuais_2027, {"Março": 200.0})
        self.assertNotIn("Anual", [row["cliente"] for row in novembro["linhas"]])
        marco = summarize_raissa_entrada(rows, start=date(2027, 3, 1), end=date(2027, 3, 31), today=today)
        self.assertEqual([row["cliente"] for row in marco["linhas"]], ["Anual", "Mensal", "Futuro"])
        self.assertEqual(
            {row["cliente"]: row["status_key"] for row in marco["linhas"]},
            {"Anual": "vai_pagar", "Mensal": "vai_pagar", "Futuro": "vai_pagar"},
        )

    def test_coluna_ah_separa_recorrente_e_renovacao_anual(self):
        from app.services.financeiro import raissa_rows_from_sheet, summarize_raissa_entrada

        headers = [f"Coluna {index}" for index in range(36)]
        headers[0] = "Empresa"
        headers[3] = "Fechamento"
        headers[31] = "Serviço"
        headers[32] = "Valor do serviço"
        headers[33] = "Outro nome"
        headers[34] = "AI"
        sheet = {
            "aba": "Raissa",
            "colunas": headers,
            "clientes": [
                {
                    "Empresa": "Todo mes",
                    "Fechamento": "10/08/2026",
                    "Valor do serviço": "59,90",
                    "Outro nome": "Boleto recorrente",
                    "AI": "Ativo",
                },
                {
                    "Empresa": "Nome engana",
                    "Fechamento": "10/08/2026",
                    "Serviço": "Oppi Anual",
                    "Valor do serviço": "10,00",
                    "Outro nome": "Cartão recorrente",
                    "AI": "Ativo",
                },
                {
                    "Empresa": "Fecha agora",
                    "Fechamento": "10/11/2026",
                    "Valor do serviço": "200,00",
                    "Outro nome": "Anual",
                    "AI": "Ativo",
                },
                {
                    "Empresa": "Desde julho",
                    "Fechamento": "15/07/2026",
                    "Valor do serviço": "400,00",
                    "Outro nome": "Plano anual",
                    "AI": "Ativo",
                },
                {
                    "Empresa": "Renova agora",
                    "Fechamento": "05/12/2025",
                    "Valor do serviço": "100,00",
                    "Outro nome": "Anual",
                    "AI": "Ativo",
                },
                {
                    "Empresa": "Parou",
                    "Fechamento": "10/11/2025",
                    "Valor do serviço": "80,00",
                    "Outro nome": "Anual",
                    "AI": "Inativo",
                },
            ],
        }
        today = date(2026, 11, 6)
        rows = raissa_rows_from_sheet(sheet, today=today)
        por_nome = {row["empresa"]: row["anual"] for row in rows}
        self.assertFalse(por_nome["Todo mes"])
        self.assertFalse(por_nome["Nome engana"])
        self.assertTrue(por_nome["Fecha agora"])
        out = summarize_raissa_entrada(rows, start=date(2026, 11, 1), end=date(2026, 11, 30), today=today)
        pizzas = {pizza["titulo"]: pizza for pizza in out["pizzas"]}
        recorrente_2026 = {fatia["mes"]: fatia["valor"] for fatia in pizzas["2026 · Recorrente"]["fatias"]}
        recorrente_2027 = {fatia["mes"]: fatia["valor"] for fatia in pizzas["2027 · Recorrente"]["fatias"]}
        anuais_2026 = {fatia["mes"]: fatia["valor"] for fatia in pizzas["2026 · Anuais"]["fatias"]}
        anuais_2027 = {fatia["mes"]: fatia["valor"] for fatia in pizzas["2027 · Anuais"]["fatias"]}
        self.assertEqual(recorrente_2026, {"Novembro": 69.9, "Dezembro": 69.9})
        self.assertEqual(recorrente_2027["Janeiro"], 69.9)
        self.assertEqual(len(recorrente_2027), 12)
        self.assertEqual(anuais_2026, {"Dezembro": 100.0})
        self.assertEqual(anuais_2027["Julho"], 400.0)
        self.assertEqual(anuais_2027["Novembro"], 200.0)
        self.assertEqual(anuais_2027["Dezembro"], 100.0)
        self.assertIn("Fecha agora", [row["cliente"] for row in out["linhas"]])

    def test_semana_situacao_manual_e_inativo_da_empresa(self):
        from app.services.financeiro import entrada_status_key, raissa_rows_from_sheet, summarize_raissa_entrada
        from app.services.legacy_core import normalize_search_text

        sheet = {
            "aba": "Raissa",
            "colunas": ["Empresa", "CNPJ", "Plano", "Fechamento", "Valor do serviço", "Status", "AI"],
            "clientes": [
                {
                    "Empresa": "Saiu",
                    "CNPJ": "",
                    "Plano": "Oppi RH",
                    "Fechamento": "02/08/2026",
                    "Valor do serviço": "10,00",
                    "Status": "",
                    "AI": "Ativo",
                    "linha": 2,
                },
                {
                    "Empresa": "Pago",
                    "CNPJ": "",
                    "Plano": "Oppi RH",
                    "Fechamento": "10/08/2026",
                    "Valor do serviço": "20,00",
                    "Status": "",
                    "AI": "Ativo",
                    "linha": 3,
                },
                {
                    "Empresa": "Aberto",
                    "CNPJ": "",
                    "Plano": "Oppi RH",
                    "Fechamento": "20/08/2026",
                    "Valor do serviço": "30,00",
                    "Status": "",
                    "AI": "Ativo",
                    "linha": 4,
                },
            ],
        }
        today = date(2026, 11, 6)
        rows = raissa_rows_from_sheet(sheet, today=today)
        chave = entrada_status_key(3, date(2026, 11, 10), "Pago", "Oppi RH")
        out = summarize_raissa_entrada(
            rows,
            start=date(2026, 11, 1),
            end=date(2026, 11, 30),
            today=today,
            overrides={chave: "ja_pagou"},
            inactive_names={normalize_search_text("Saiu")},
        )
        self.assertEqual([row["cliente"] for row in out["linhas"]], ["Aberto", "Pago"])
        pago = next(row for row in out["linhas"] if row["cliente"] == "Pago")
        self.assertEqual(pago["status_key"], "ja_pagou")
        self.assertTrue(pago["manual"])
        self.assertEqual(out["ja_pagou_n"], 1)
        self.assertEqual(out["vai_pagar_n"], 1)
        semanas = {semana["label"]: [row["cliente"] for row in semana["linhas"]] for semana in out["semanas"]}
        self.assertEqual(semanas["Semana 2 · dias 8 a 14 de novembro"], ["Pago"])
        self.assertEqual(semanas["Semana 3 · dias 15 a 21 de novembro"], ["Aberto"])
        from app.services.financeiro import _apply_week_choice

        _apply_week_choice(out, {}, date(2026, 11, 1), date(2026, 11, 30), today)
        self.assertEqual(out["semana"], 1)
        self.assertEqual(out["semanas"], [])
        opcoes = [item["label"] for item in out["semanas_opcoes"]]
        self.assertEqual(opcoes[1], "Primeira · 1 a 7")
        self.assertEqual(opcoes[2], "Segunda · 8 a 14")
        de_novo = summarize_raissa_entrada(
            rows,
            start=date(2026, 11, 1),
            end=date(2026, 11, 30),
            today=today,
            overrides={chave: "ja_pagou"},
            inactive_names={normalize_search_text("Saiu")},
        )
        _apply_week_choice(de_novo, {"semana": "2"}, date(2026, 11, 1), date(2026, 11, 30), today)
        self.assertEqual([row["cliente"] for semana in de_novo["semanas"] for row in semana["linhas"]], ["Pago"])
        pizzas = {pizza["titulo"]: pizza for pizza in out["pizzas"]}
        novembro = {fatia["mes"]: fatia["valor"] for fatia in pizzas["2026 · Recorrente"]["fatias"]}
        self.assertEqual(novembro["Novembro"], 50.0)

    def test_coluna_g_filial_nao_entra_na_relacao(self):
        from app.services.financeiro import raissa_rows_from_sheet, summarize_raissa_entrada

        sheet = {
            "aba": "Raissa",
            "colunas": ["Empresa", "CNPJ", "Plano", "Fechamento", "Valor do serviço", "Status", "É filial", "AI"],
            "clientes": [
                {
                    "Empresa": "Escola Matriz",
                    "CNPJ": "",
                    "Plano": "Oppi RH",
                    "Fechamento": "10/08/2026",
                    "Valor do serviço": "59,90",
                    "Status": "",
                    "É filial": "Matriz",
                    "AI": "Ativo",
                },
                {
                    "Empresa": "Escola Loja",
                    "CNPJ": "",
                    "Plano": "Oppi RH",
                    "Fechamento": "10/08/2026",
                    "Valor do serviço": "59,90",
                    "Status": "",
                    "É filial": "Filial",
                    "AI": "Ativo",
                },
            ],
        }
        today = date(2026, 11, 6)
        rows = raissa_rows_from_sheet(sheet, today=today)
        self.assertEqual([row["empresa"] for row in rows if row["filial"]], ["Escola Loja"])
        out = summarize_raissa_entrada(rows, start=date(2026, 11, 1), end=date(2026, 11, 30), today=today)
        self.assertEqual([row["cliente"] for row in out["linhas"]], ["Escola Matriz"])
        self.assertEqual(out["total_n"], 1)

    def test_situacao_salva_continua_no_cliente(self):
        import os
        import tempfile

        from app.services.crm_local_db import load_entrada_statuses, save_entrada_status
        from app.services.financeiro import entrada_status_key, raissa_rows_from_sheet, summarize_raissa_entrada

        sheet = {
            "aba": "Raissa",
            "colunas": ["Empresa", "CNPJ", "Plano", "Fechamento", "Valor do serviço", "Status", "AI"],
            "clientes": [
                {
                    "Empresa": "Pago",
                    "CNPJ": "18.088.253/0001-24",
                    "Plano": "Oppi RH",
                    "Fechamento": "10/08/2026",
                    "Valor do serviço": "20,00",
                    "Status": "",
                    "AI": "Ativo",
                    "linha": 8,
                },
            ],
        }
        today = date(2026, 11, 6)
        rows = raissa_rows_from_sheet(sheet, today=today)
        chave = entrada_status_key(8, date(2026, 11, 10), "Pago", "Oppi RH", "18.088.253/0001-24")
        self.assertTrue(chave.startswith("pag:"))
        previous = os.environ.get("APP_STORAGE_DIR")
        os.environ["APP_STORAGE_DIR"] = tempfile.mkdtemp()
        try:
            self.assertTrue(save_entrada_status(chave, "ja_pagou"))
            self.assertEqual(load_entrada_statuses().get(chave), "ja_pagou")
            out = summarize_raissa_entrada(
                rows,
                start=date(2026, 11, 1),
                end=date(2026, 11, 30),
                today=today,
                overrides=load_entrada_statuses(),
            )
        finally:
            if previous is None:
                os.environ.pop("APP_STORAGE_DIR", None)
            else:
                os.environ["APP_STORAGE_DIR"] = previous
        self.assertEqual(out["linhas"][0]["status_key"], "ja_pagou")
        self.assertEqual(out["linhas"][0]["chave"], chave)
        self.assertTrue(out["linhas"][0]["manual"])

    def test_nao_pago_nao_vira_pago_e_data_passada_fica_pendente(self):
        from app.services.financeiro import classify_raissa_entry

        today = date(2026, 11, 6)
        self.assertEqual(classify_raissa_entry("Não pago", date(2026, 11, 1), today)["key"], "pendente")
        self.assertEqual(classify_raissa_entry("Pago", date(2026, 11, 1), today)["key"], "ja_pagou")
        self.assertEqual(classify_raissa_entry("1 Boleto Aguardando", date(2026, 11, 20), today)["key"], "pendente")
        self.assertEqual(classify_raissa_entry("", date(2026, 11, 20), today, "Ativo")["key"], "vai_pagar")
        self.assertEqual(classify_raissa_entry("Pago", date(2026, 11, 20), today, "Inativo")["key"], "cancelado")
        self.assertEqual(classify_raissa_entry("", None, today, "INATIVO")["key"], "cancelado")
        self.assertEqual(classify_raissa_entry("", None, today, "Ativo")["key"], "pendente")

    def test_entrada_nao_consulta_o_asaas(self):
        from unittest.mock import patch

        from app.services.financeiro import build_financeiro_context

        sheet = self._sheet()
        with (
            patch("app.services.campaign_leads.read_raissa_sheet", return_value=sheet),
            patch("app.services.financeiro.fetch_dashboard_payload") as asaas,
            patch("app.services.financeiro.load_boleto_entrada") as boletos,
        ):
            ctx = build_financeiro_context(
                {"tab": "entradas", "period_start": "2026-11-01", "period_end": "2026-11-30"},
                use_raissa=True,
            )
        asaas.assert_not_called()
        boletos.assert_not_called()
        self.assertEqual(ctx["kpi_cards"][0]["label"], "Ativos")
        self.assertEqual(ctx["controle"]["aba"], "Raissa")
        self.assertEqual(ctx["filters"]["period_start"], "2026-11-01")

    def test_contas_a_pagar_nao_consulta_o_asaas(self):
        from unittest.mock import patch

        from app.services.financeiro import build_financeiro_context

        with (
            patch("app.services.financeiro.fetch_dashboard_payload") as asaas,
            patch("app.services.financeiro.build_internal_forecast") as previsao,
            patch("app.services.financeiro.list_payables", return_value=[]),
            patch("app.services.financeiro.list_suppliers", return_value=[]),
            patch("app.services.campaign_leads.read_raissa_sheet", return_value={"aba": "Raissa", "colunas": [], "clientes": []}),
            patch("app.services.financeiro.load_inactive_companies", return_value=(set(), set())),
            patch("app.services.crm_local_db.load_entrada_statuses", return_value={}),
        ):
            ctx = build_financeiro_context(
                {"tab": "pagar", "period_start": "2026-11-01", "period_end": "2026-11-30"},
            )
        asaas.assert_not_called()
        previsao.assert_not_called()
        self.assertEqual(ctx["tab"], "pagar")
        self.assertEqual(ctx["kpi_cards"][0]["label"], "A pagar")
        self.assertEqual(ctx["payables"]["semanas_opcoes"][0]["label"], "Mês inteiro")
        self.assertEqual(ctx["filters"]["period_start"], "2026-11-01")


if __name__ == "__main__":
    unittest.main(verbosity=2)
