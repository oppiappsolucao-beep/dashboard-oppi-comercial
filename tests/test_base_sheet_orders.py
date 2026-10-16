import unittest

from app.services.campaign_leads import is_lead_worksheet_title, leads_from_values, _pick_columns
from app.services.representative_prospects import merge_prospect_rows
from app.services.ticket_orders import is_ticket_worksheet_title, ticket_rows_from_values


class BaseSheetOrderTests(unittest.TestCase):
    def test_titulo_e_nome_da_empresa_ainda_viram_lead(self):
        leads = leads_from_values(
            [
                ["Leads da base"],
                ["Nome da empresa", "Celular / WhatsApp", "UF", "Data de abertura", "Data"],
                ["Oficina Sol", "11988887777", "SP", "01/01/2020", "16/10/2026"],
            ]
        )
        self.assertEqual(len(leads), 1)
        self.assertEqual(leads[0]["empresa"], "Oficina Sol")
        self.assertEqual(leads[0]["phone"], "11988887777")
        self.assertEqual(leads[0]["uf"], "SP")
        self.assertEqual(leads[0]["lead_date"], "2026-10-16")
        self.assertEqual(leads[0]["sheet_row"], 3)

    def test_data_de_abertura_nao_substitui_a_data_do_lead(self):
        columns = _pick_columns(["Empresa", "Data de abertura", "Data"])
        self.assertEqual(columns["lead_date"], 2)

    def test_aba_de_clientes_nao_e_aba_de_lead(self):
        self.assertFalse(is_lead_worksheet_title("Raissa"))
        self.assertTrue(is_lead_worksheet_title("Leads Raissa"))
        self.assertTrue(is_lead_worksheet_title("Leads Raissa 01"))

    def test_aba_base_com_titulo_vira_chamado(self):
        rows = ticket_rows_from_values(
            [
                ["Base"],
                ["Nome da empresa", "Telefone", "Assunto"],
                ["Mercado Azul", "11977776666", "Quero o ponto"],
            ]
        )
        self.assertEqual(rows[0]["empresa"], "Mercado Azul")
        self.assertEqual(rows[0]["phone"], "11977776666")
        self.assertEqual(rows[0]["subject"], "Quero o ponto")
        self.assertEqual(rows[0]["sheet_row"], 3)
        self.assertTrue(is_ticket_worksheet_title("Base"))
        self.assertTrue(is_ticket_worksheet_title("Planilha de base"))
        self.assertFalse(is_ticket_worksheet_title("Raissa"))

    def test_planilha_do_representante_guarda_a_os(self):
        rows = merge_prospect_rows(
            [
                {
                    "tab_title": "São Paulo_José_01",
                    "sheet_row": 2,
                    "empresa": "Oficina Sol",
                    "cnpj": "18.088.253/0001-24",
                    "telefone": "11988887777",
                    "uf": "SP",
                    "queue": "contato",
                    "registered": 0,
                    "registration_row": 0,
                    "order_id": "os_abc",
                }
            ],
            [
                {
                    "tab_title": "São Paulo_José_01",
                    "sheet_row": 2,
                    "empresa": "Oficina Sol",
                    "cnpj": "18.088.253/0001-24",
                    "telefone": "11988887777",
                    "uf": "SP",
                }
            ],
        )
        self.assertEqual(rows[0]["order_id"], "os_abc")
        self.assertEqual(rows[0]["queue"], "contato")


if __name__ == "__main__":
    unittest.main()
