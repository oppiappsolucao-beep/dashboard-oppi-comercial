"""Regras da limpeza: 9º dígito Evolution e duplicados sem apagar filial."""
from __future__ import annotations

import unittest

from app.services.crm_phone_cleanup import (
    connected_duplicate_groups,
    conversation_phone_from_remote_jid,
    is_evolution_origin,
    is_protected_filial,
    needs_mobile_ninth_digit,
    pick_duplicate_deletions,
)
from app.services.legacy_core import canonicalize_br_mobile_national, format_br_whatsapp_display


class NinthDigitTest(unittest.TestCase):
    def test_inserts_nine_on_old_mobile(self):
        self.assertTrue(needs_mobile_ninth_digit("1198765432"))
        self.assertEqual(canonicalize_br_mobile_national("1198765432"), "11998765432")
        self.assertEqual(format_br_whatsapp_display("1198765432"), "(11) 99876-5432")

    def test_keeps_complete_mobile(self):
        self.assertFalse(needs_mobile_ninth_digit("(11) 99876-5432"))
        self.assertEqual(canonicalize_br_mobile_national("11998765432"), "11998765432")

    def test_landline_does_not_gain_nine(self):
        self.assertFalse(needs_mobile_ninth_digit("1133334444"))
        self.assertEqual(canonicalize_br_mobile_national("1133334444"), "1133334444")

    def test_strips_ddi_then_inserts_nine(self):
        self.assertTrue(needs_mobile_ninth_digit("551198765432"))
        self.assertEqual(canonicalize_br_mobile_national("551198765432"), "11998765432")


class EvolutionOriginTest(unittest.TestCase):
    def test_from_attendance_or_obs(self):
        self.assertTrue(is_evolution_origin({"from_attendance": True, "empresa": "Padaria"}))
        self.assertTrue(
            is_evolution_origin(
                {"observacoes": "Lead criado automaticamente pelo Atendimento WhatsApp."}
            )
        )
        self.assertTrue(is_evolution_origin({"empresa": "Lead WhatsApp (11) 99999-0000"}))
        self.assertFalse(is_evolution_origin({"empresa": "Padaria Central", "observacoes": ""}))


class FilialAndDuplicatesTest(unittest.TestCase):
    def test_filial_is_protected(self):
        self.assertTrue(is_protected_filial({"is_filial": True}))
        self.assertTrue(is_protected_filial({"empresa_matriz_sheet_row": 12}))
        self.assertTrue(is_protected_filial({"empresa": "King Maki Filial Centro"}))
        self.assertFalse(is_protected_filial({"empresa": "King Maki Matriz", "is_filial": False}))

    def test_does_not_delete_filial_against_matriz(self):
        matriz = {
            "id": 1,
            "sheet_row": 10,
            "empresa": "Padaria Matriz",
            "cadastro_tipo": "empresa",
            "cnpj": "12345678000199",
            "is_filial": False,
        }
        filial = {
            "id": 2,
            "sheet_row": 11,
            "empresa": "Padaria Filial",
            "cadastro_tipo": "empresa",
            "cnpj": "12345678000199",
            "is_filial": True,
            "empresa_matriz_sheet_row": 10,
        }
        keeper, doomed = pick_duplicate_deletions([matriz, filial])
        self.assertEqual(keeper["id"], 1)
        self.assertEqual(doomed, [])

    def test_deletes_weaker_lead_keeps_company(self):
        lead = {
            "id": 1,
            "sheet_row": 20,
            "empresa": "Lead WhatsApp 1199",
            "cadastro_tipo": "lead",
            "telefone_b2b": "1198765432",
        }
        empresa = {
            "id": 2,
            "sheet_row": 21,
            "empresa": "Padaria Bom Pão",
            "cadastro_tipo": "empresa",
            "cnpj": "11222333000181",
            "endereco": "Rua A",
            "telefone_b2b": "(11) 99876-5432",
        }
        keeper, doomed = pick_duplicate_deletions([lead, empresa])
        self.assertEqual(keeper["id"], 2)
        self.assertEqual([row["id"] for row in doomed], [1])

    def test_groups_with_and_without_ninth_digit(self):
        a = {"id": 1, "telefone_b2b": "1198765432", "cnpj": ""}
        b = {"id": 2, "telefone_b2b": "(11) 99876-5432", "cnpj": ""}
        groups = connected_duplicate_groups([a, b])
        self.assertEqual(len(groups), 1)
        self.assertEqual({row["id"] for row in groups[0]}, {1, 2})

    def test_send_phone_follows_pn_jid_not_lid(self):
        self.assertEqual(
            conversation_phone_from_remote_jid("551691378494@s.whatsapp.net"),
            "551691378494",
        )
        self.assertEqual(conversation_phone_from_remote_jid("123456789012345@lid"), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
