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
        pizzas = {pizza["ano"]: pizza for pizza in novembro["pizzas"]}
        fatias_2026 = {fatia["mes"]: fatia["valor"] for fatia in pizzas[2026]["fatias"]}
        fatias_2027 = {fatia["mes"]: fatia["valor"] for fatia in pizzas[2027]["fatias"]}
        self.assertEqual(fatias_2026, {"Novembro": 59.9, "Dezembro": 119.8})
        self.assertEqual(fatias_2027["Janeiro"], 119.8)
        self.assertEqual(fatias_2027["Março"], 319.8)
        self.assertEqual(fatias_2027["Dezembro"], 119.8)
        self.assertEqual(len(fatias_2027), 12)
        self.assertNotIn("Anual", [row["cliente"] for row in novembro["linhas"]])
        marco = summarize_raissa_entrada(rows, start=date(2027, 3, 1), end=date(2027, 3, 31), today=today)
        self.assertEqual([row["cliente"] for row in marco["linhas"]], ["Anual", "Mensal", "Futuro"])
        self.assertEqual(
            {row["cliente"]: row["status_key"] for row in marco["linhas"]},
            {"Anual": "vai_pagar", "Mensal": "vai_pagar", "Futuro": "vai_pagar"},
        )

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
