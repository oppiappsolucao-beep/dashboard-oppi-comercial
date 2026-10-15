"""Aba Estado_Nome_01 liga o representante e o cadastro sai com vendedor para a Raissa."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.raissa_company_sync import apply_raissa_values, payload_for_raissa
from app.services.org_registry import login_accesses
from app.services.representative_prospects import (
    RAISSA_NEW_STATUS,
    build_prospect_board,
    closed_companies,
    merge_prospect_rows,
    parse_prospect_tab,
    read_prospect_rows,
    registration_fields_from_prospect,
    seller_for_representative,
    tab_matches_representative,
)


JOSE = {
    "kind": "representante",
    "name": "José",
    "state_name": "São Paulo",
    "region": "SP",
}


class RepresentativeProspectsTest(unittest.TestCase):
    def test_representante_entra_no_cadastro(self):
        self.assertEqual(
            login_accesses("representante", ["gestao", "financeiro", "empresas"]),
            ["kanban", "novo_cadastro", "empresas"],
        )
        self.assertEqual(login_accesses("funcionario", ["kanban", "agenda"]), ["kanban", "agenda"])

    def test_le_aba_sao_paulo_jose(self):
        parsed = parse_prospect_tab("São Paulo_José_01")
        self.assertEqual(parsed["state"], "São Paulo")
        self.assertEqual(parsed["representative"], "José")
        self.assertEqual(parsed["batch"], "01")
        self.assertTrue(tab_matches_representative("São Paulo_José_01", JOSE))
        self.assertFalse(tab_matches_representative("Pará_Raul_01", JOSE))

    def test_le_as_quatro_colunas_da_planilha_nova(self):
        rows = read_prospect_rows(
            [
                ["Nome da empresa", "CNPJ", "Telefone", "UF"],
                ["Oficina Sol", "18.088.253/0001-24", "11988887777", "SP"],
                ["", "", "", ""],
            ]
        )
        self.assertEqual(len(rows), 1)
        fields = registration_fields_from_prospect(rows[0], "José")
        self.assertEqual(fields["empresa"], "Oficina Sol")
        self.assertEqual(fields["cnpj"], "18.088.253/0001-24")
        self.assertEqual(fields["telefone_b2b"], "11988887777")
        self.assertEqual(fields["uf"], "SP")
        self.assertEqual(fields["vendedor"], "José")

    def test_kanban_separa_lista_e_empresa_fechada(self):
        incoming = [
            {"tab_title": "São Paulo_José_01", "sheet_row": 2, "empresa": "Oficina Sol", "cnpj": "18.088.253/0001-24", "telefone": "11988887777", "uf": "SP"},
            {"tab_title": "São Paulo_José_01", "sheet_row": 3, "empresa": "Mercado Azul", "cnpj": "22.222.222/0001-22", "telefone": "11977776666", "uf": "SP"},
        ]
        existing = [
            {"tab_title": "São Paulo_José_01", "sheet_row": 2, "empresa": "Oficina Sol", "cnpj": "18.088.253/0001-24", "telefone": "11988887777", "uf": "SP", "queue": "proposta", "registered": 0, "registration_row": 0},
        ]
        rows = merge_prospect_rows(existing, incoming)
        oficina = next(row for row in rows if row["empresa"] == "Oficina Sol")
        self.assertEqual(oficina["queue"], "proposta")
        board = build_prospect_board(rows)
        self.assertEqual(board[0]["name"], "Lista")
        self.assertEqual([card["empresa"] for card in board[0]["cards"]], ["Mercado Azul"])
        self.assertEqual([card["empresa"] for card in board[2]["cards"]], ["Oficina Sol"])
        oficina["registered"] = 1
        self.assertEqual([row["empresa"] for row in closed_companies(rows)], ["Oficina Sol"])
        self.assertEqual([card["empresa"] for column in build_prospect_board(rows) for card in column["cards"]], ["Mercado Azul"])

    def test_salvar_do_representante_grava_vendedor_e_status_na_raissa(self):
        self.assertEqual(seller_for_representative({"org_person_kind": "representante", "org_person_name": "José"}), "José")
        self.assertEqual(seller_for_representative({"org_person_kind": "funcionario", "org_person_name": "Ana"}), "")
        payload = payload_for_raissa(registration_fields_from_prospect(
            {"empresa": "Oficina Sol", "cnpj": "18.088.253/0001-24", "telefone": "11988887777", "uf": "SP"},
            "José",
        ))
        payload["status_cadastro"] = RAISSA_NEW_STATUS
        headers = ["Empresa", "CNPJ", "WhatsApp", "UF", "Vendedor", "Status"]
        row = apply_raissa_values([""] * len(headers), headers, payload, only_filled=False)
        self.assertEqual(row[0], "Oficina Sol")
        self.assertEqual(row[2], "11988887777")
        self.assertEqual(row[4], "José")
        self.assertEqual(row[5], "1 Boleto Aguardando")


if __name__ == "__main__":
    unittest.main()
