"""Aba Raissa recebe dados gerais e financeiro do cadastro, sem apagar o que já existe."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.raissa_company_sync import (
    apply_raissa_values,
    ensure_raissa_columns,
    match_cadastro_sheet_rows,
    payload_for_raissa,
)


class RaissaCompanySyncTest(unittest.TestCase):
    def test_preenche_dados_gerais_e_financeiro_pelos_cabecalhos(self):
        headers = ["Empresa", "CNPJ", "WhatsApp", "Nicho", "Plano", "Valor", "Forma de pagamento"]
        payload = payload_for_raissa(
            {
                "empresa": "BRASIL AUTO PECAS",
                "cnpj": "18.088.253/0001-24",
                "telefone_b2b": "11999998888",
                "nicho": "Automotivo",
                "email": "contato@brasil.com",
            },
            {"servico": "Oppi Ponto Mensal", "valor": "R$ 197,00", "forma": "boleto_recorrente", "ciclo": "mensal"},
        )
        row = apply_raissa_values([""] * len(headers), headers, payload, only_filled=False)
        self.assertEqual(row[0], "BRASIL AUTO PECAS")
        self.assertEqual(row[1], "18.088.253/0001-24")
        self.assertEqual(row[2], "11999998888")
        self.assertEqual(row[3], "Automotivo")
        self.assertEqual(row[4], "Oppi Ponto Mensal")
        self.assertEqual(row[5], "R$ 197,00")
        self.assertEqual(row[6], "Boleto recorrente")

    def test_atualiza_email_de_cobranca_e_coluna_cobranca_whatsapp(self):
        headers = ["Empresa", "E-mail de cobrança", "Cobrança / WhatsApp", "WhatsApp"]
        payload = payload_for_raissa(
            {
                "empresa": "CLEAR SERVICOS JURIDICOS LTDA",
                "email_cobranca": "financeiro@clear.com",
                "telefone_b2b": "11988887777",
            }
        )
        row = apply_raissa_values(
            ["CLEAR SERVICOS JURIDICOS LTDA", "antigo@clear.com", "11911112222", "11911112222"],
            headers,
            payload,
            only_filled=True,
        )
        self.assertEqual(row[1], "financeiro@clear.com")
        self.assertEqual(row[2], "11988887777")
        self.assertEqual(row[3], "11988887777")

    def test_atualiza_email_de_cobranca_com_barra_ou_para(self):
        headers = ["Empresa", "Cobrança / E-mail", "E-mail para cobrança", "E-mail"]
        payload = payload_for_raissa(
            {
                "empresa": "CLEAR SERVICOS JURIDICOS LTDA",
                "email": "contato@clear.com",
                "email_cobranca": "financeiro@clear.com",
            }
        )
        row = apply_raissa_values(
            ["CLEAR SERVICOS JURIDICOS LTDA", "antigo@clear.com", "antigo@clear.com", "contato@clear.com"],
            headers,
            payload,
            only_filled=True,
        )
        self.assertEqual(row[1], "financeiro@clear.com")
        self.assertEqual(row[2], "financeiro@clear.com")
        self.assertEqual(row[3], "contato@clear.com")

    def test_atualizacao_nao_apaga_celula_vazia(self):
        headers = ["Empresa", "CNPJ", "Plano"]
        row = apply_raissa_values(
            ["BRASIL AUTO PECAS", "18.088.253/0001-24", "Oppi Ponto Mensal"],
            headers,
            {"empresa": "BRASIL AUTO PECAS", "cnpj": "", "plano": ""},
            only_filled=True,
        )
        self.assertEqual(row[1], "18.088.253/0001-24")
        self.assertEqual(row[2], "Oppi Ponto Mensal")

    def test_cria_colunas_de_financeiro_se_faltarem(self):
        headers = ensure_raissa_columns(["Empresa", "CNPJ"])
        self.assertIn("Plano", headers)
        self.assertIn("Valor", headers)
        self.assertIn("Nicho", headers)

    def test_liga_empresa_da_raissa_ao_cadastro_pelo_cnpj_ou_nome(self):
        empresas = [
            {"empresa": "Brasil Auto Peças", "cnpj": "18.088.253/0001-24"},
            {"empresa": "Clínica Sol", "cnpj": ""},
            {"empresa": "Nome Repetido", "cnpj": ""},
        ]
        cadastros = [
            {"sheet_row": 12, "empresa": "JOSE VALTON", "nome_fantasia": "BRASIL AUTO PECAS", "cnpj": "18088253000124"},
            {"sheet_row": 40, "empresa": "Clinica Sol", "nome_fantasia": "", "cnpj": ""},
            {"sheet_row": 7, "empresa": "Nome Repetido", "nome_fantasia": "", "cnpj": ""},
            {"sheet_row": 8, "empresa": "Nome Repetido", "nome_fantasia": "", "cnpj": ""},
        ]
        linked = match_cadastro_sheet_rows(empresas, cadastros)
        self.assertEqual(linked[0]["sheet_row"], 12)
        self.assertEqual(linked[1]["sheet_row"], 40)
        self.assertEqual(linked[2]["sheet_row"], 8)

    def test_liga_pelo_telefone_quando_o_numero_e_unico(self):
        empresas = [{"empresa": "Outro Nome", "cnpj": "", "telefone": "(11) 98888-7766"}]
        cadastros = [
            {"sheet_row": 3, "empresa": "Academia Alfa", "cnpj": "", "telefones": ["11988887766"]},
            {"sheet_row": 9, "empresa": "Academia Beta", "cnpj": "", "telefones": ["11988887766"]},
        ]
        same_phone = match_cadastro_sheet_rows(empresas, cadastros)
        self.assertEqual(same_phone[0]["sheet_row"], 0)
        unique = match_cadastro_sheet_rows(
            empresas,
            [{"sheet_row": 3, "empresa": "Academia Alfa", "cnpj": "", "telefones": ["11988887766"]}],
        )
        self.assertEqual(unique[0]["sheet_row"], 3)


if __name__ == "__main__":
    unittest.main()
