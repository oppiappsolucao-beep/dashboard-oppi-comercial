"""Escolha do banco e payloads do Mercado Pago, sem chamar a API."""
from __future__ import annotations

import hashlib
import hmac
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.cadastro_billing import parse_billing_plan_from_form
from app.services.mercadopago_client import (
    build_boleto_body,
    build_card_preference_body,
    build_pix_subscription_body,
    charge_link,
    sheet_row_from_reference,
    verify_webhook_signature,
)


class MercadoPagoBillingTest(unittest.TestCase):
    def test_cliente_atual_continua_no_asaas(self):
        plan = parse_billing_plan_from_form({
            "billing_ciclo": "mensal",
            "billing_forma": "boleto_recorrente",
            "billing_valor": "R$ 59,90",
        })
        self.assertEqual(plan["banco"], "asaas")
        self.assertEqual(plan["forma"], "boleto_recorrente")

    def test_mercado_pago_na_contratacao_e_na_troca_para_anual(self):
        from app.services.cadastro_billing import BILLING_FORM_OPTIONS, MERCADO_PAGO_FORM_OPTIONS

        self.assertEqual(MERCADO_PAGO_FORM_OPTIONS, BILLING_FORM_OPTIONS)
        novo = parse_billing_plan_from_form({
            "billing_banco": "mercado_pago",
            "billing_ciclo": "mensal",
            "billing_forma": "pix_recorrente",
            "billing_valor": "197,00",
        })
        self.assertEqual(novo["banco"], "mercado_pago")
        self.assertEqual(novo["forma"], "pix_recorrente")
        self.assertEqual(novo["forma_label"], "Pix recorrente")
        anual = parse_billing_plan_from_form({
            "billing_banco": "mercado_pago",
            "billing_ciclo": "anual",
            "billing_forma": "cartao_recorrente",
            "billing_valor": "1.200,00",
        })
        self.assertEqual(anual["ciclo"], "anual")
        self.assertEqual(anual["forma_label"], "Cartão recorrente")

    def test_boleto_e_cobranca_unica_com_cnpj(self):
        body = build_boleto_body(
            amount=59.9,
            description="Oppi Ponto",
            due="2026-12-05",
            email="financeiro@acme.com",
            empresa="AUDIOFILM AUTO CENTER LTDA",
            cnpj="11222333000181",
            sheet_row=40,
            notification_url="https://comercial.oppitech.com.br/webhooks/mercadopago",
        )
        self.assertEqual(body["payment_method_id"], "bolbradesco")
        self.assertEqual(body["external_reference"], "crm-40")
        self.assertEqual(body["payer"]["identification"]["number"], "11222333000181")
        self.assertNotIn("auto_recurring", body)

    def test_cartao_anual_gera_link_sem_guardar_cartao(self):
        body = build_card_preference_body(
            amount=1200,
            description="Plano anual",
            email="financeiro@acme.com",
            sheet_row=40,
            notification_url="https://comercial.oppitech.com.br/webhooks/mercadopago",
            back_url="https://comercial.oppitech.com.br",
        )
        self.assertEqual(body["items"][0]["unit_price"], 1200)
        self.assertEqual(body["payment_methods"]["installments"], 1)
        self.assertNotIn("card_token_id", body)

    def test_pix_automatico_e_assinatura_mensal(self):
        body = build_pix_subscription_body(
            amount=197,
            description="Oppi Ponto",
            email="financeiro@acme.com",
            due="2026-12-05",
            ciclo="mensal",
            sheet_row=40,
            notification_url="https://comercial.oppitech.com.br/webhooks/mercadopago",
            back_url="https://comercial.oppitech.com.br",
        )
        self.assertEqual(body["status"], "pending")
        self.assertEqual(body["auto_recurring"]["frequency"], 1)
        self.assertEqual(body["auto_recurring"]["frequency_type"], "months")
        self.assertTrue(charge_link({"init_point": "https://www.mercadopago.com.br/subscriptions/checkout?preapproval_id=abc"}).startswith("https://"))

    def test_aviso_encontra_o_cadastro_e_confere_a_assinatura(self):
        self.assertEqual(sheet_row_from_reference("crm-40"), 40)
        secret = "segredo"
        manifest = "id:99;request-id:req-1;ts:1000;"
        digest = hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()
        self.assertTrue(verify_webhook_signature(
            secret=secret,
            data_id="99",
            request_id="req-1",
            signature_header=f"ts=1000,v1={digest}",
        ))
        self.assertFalse(verify_webhook_signature(
            secret=secret,
            data_id="99",
            request_id="req-1",
            signature_header="ts=1000,v1=errado",
        ))


if __name__ == "__main__":
    unittest.main()
