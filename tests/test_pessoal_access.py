"""A aba Pessoal só abre para o setor Financeiro 1."""
from __future__ import annotations

import unittest


class _Request:
    def __init__(self, session: dict, path: str = "/financeiro/pessoal"):
        self.session = session
        self.url = type("Url", (), {"path": path})()


class PessoalAccessTest(unittest.TestCase):
    def test_so_o_setor_financeiro_1_ve_a_aba(self):
        from app.services.access_scope import nav_permissions, pessoal_allowed

        financeiro_1 = _Request({
            "authenticated": True,
            "org_person_id": "p1",
            "org_sector_name": "Financeiro 1",
            "org_accesses": ["financeiro"],
        })
        financeiro = _Request({
            "authenticated": True,
            "org_person_id": "p2",
            "org_sector_name": "Financeiro",
            "org_accesses": ["financeiro"],
        })
        outro = _Request({
            "authenticated": True,
            "org_person_id": "p3",
            "org_sector_name": "Comercial",
            "org_accesses": ["kanban"],
        })
        admin = _Request({"authenticated": True, "username": "oppitech", "user_role": "Administrador"})
        oppi = _Request({"authenticated": True, "username": "oppi", "user_role": "Administrador"})
        vendedor = _Request({"authenticated": True, "username": "higo", "user_role": "Vendedor"})

        self.assertTrue(pessoal_allowed(financeiro_1))
        self.assertTrue(pessoal_allowed(_Request({
            "authenticated": True,
            "org_person_id": "p1",
            "username": "financeiro1",
            "org_sector_name": "Financeiro",
        })))
        self.assertFalse(pessoal_allowed(admin))
        self.assertFalse(pessoal_allowed(oppi))
        self.assertFalse(pessoal_allowed(financeiro))
        self.assertFalse(pessoal_allowed(_Request({
            "authenticated": True,
            "org_person_id": "p9",
            "org_sector_name": "Financeiro 2",
        })))
        self.assertFalse(pessoal_allowed(outro))
        self.assertFalse(pessoal_allowed(vendedor))
        self.assertFalse(pessoal_allowed(_Request({})))

        self.assertTrue(nav_permissions(financeiro_1)["pessoal"])
        self.assertFalse(nav_permissions(admin)["pessoal"])
        self.assertFalse(nav_permissions(oppi)["pessoal"])
        self.assertFalse(nav_permissions(financeiro)["pessoal"])
        self.assertFalse(nav_permissions(vendedor)["pessoal"])
        self.assertTrue(nav_permissions(admin)["financeiro"])

    def test_endereco_direto_nao_abre_para_os_outros(self):
        from app.services.access_scope import enforce_access

        bloqueado = enforce_access(
            _Request({
                "authenticated": True,
                "org_person_id": "p2",
                "org_sector_name": "Financeiro",
                "org_accesses": ["financeiro"],
            })
        )
        self.assertIsNotNone(bloqueado)
        self.assertIsNone(
            enforce_access(
                _Request({
                    "authenticated": True,
                    "org_person_id": "p1",
                    "org_sector_name": "Financeiro 1",
                    "org_accesses": [],
                })
            )
        )
        self.assertIsNone(enforce_access(_Request({})))

    def test_conta_pessoal_nao_cai_na_tabela_da_empresa(self):
        from config.settings import settings

        if (settings.database_url or "").startswith("postgresql"):
            self.skipTest("este teste usa o arquivo local")
        import os
        import tempfile
        from datetime import date

        os.environ["APP_STORAGE_DIR"] = tempfile.mkdtemp()
        try:
            from app.services.company_payables import list_payables as contas_da_empresa
            from app.services.personal_payables import create_payable, delete_payable, list_payables

            criado = create_payable("Aluguel casa", 80, date(2026, 11, 5), supplier="Casa", category="fixa")
            pessoal = list_payables(date(2026, 11, 1), date(2026, 11, 30), date(2026, 11, 12))
            empresa = contas_da_empresa(date(2026, 11, 1), date(2026, 11, 30), date(2026, 11, 12))
            self.assertEqual([row["description"] for row in pessoal], ["Aluguel casa"])
            self.assertNotIn("Aluguel casa", [row["description"] for row in empresa])
            delete_payable(criado)
        finally:
            os.environ.pop("APP_STORAGE_DIR", None)
