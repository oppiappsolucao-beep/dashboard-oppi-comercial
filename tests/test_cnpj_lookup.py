"""Nicho pelo CNAE e um único responsável no preenchimento do CNPJ."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.cnpj_lookup import niche_from_cnae, registration_from_cnpj_payload


class CnpjLookupTest(unittest.TestCase):
    def test_auto_pecas_cnae_seleciona_automotivo(self):
        niche = niche_from_cnae(
            "4530703",
            "Comércio a varejo de peças e acessórios novos para veículos automotores",
        )
        self.assertEqual(niche, "Automotivo")

    def test_farmacia_nao_cai_em_comercio(self):
        niche = niche_from_cnae(
            "4771701",
            "Comércio varejista de produtos farmacêuticos, sem manipulação de fórmulas",
        )
        self.assertEqual(niche, "Saúde e Bem-estar")

    def test_software_seleciona_tecnologia(self):
        self.assertEqual(
            niche_from_cnae("6201501", "Desenvolvimento de programas de computador sob encomenda"),
            "Tecnologia",
        )

    def test_preenche_um_responsavel_com_email_e_senha(self):
        payload = registration_from_cnpj_payload(
            {
                "nome_fantasia": "BRASIL AUTO PECAS",
                "razao_social": "BRASIL AUTO PECAS LTDA",
                "data_inicio_atividade": "2013-05-10",
                "capital_social": 50000,
                "cnae_fiscal": 4530703,
                "cnae_fiscal_descricao": "Comércio a varejo de peças e acessórios novos para veículos automotores",
                "email": "contato@brasilautopecas.com.br",
                "ddd_telefone_1": "1133334444",
                "qsa": [
                    {"nome_socio": "MARIA SOUZA", "codigo_qualificacao_socio": 22, "cnpj_cpf_do_socio": "***111222**"},
                    {"nome_socio": "JOAO LIMA", "codigo_qualificacao_socio": 49, "cnpj_cpf_do_socio": "12345678901"},
                    {"nome_socio": "PEDRO ALVES", "codigo_qualificacao_socio": 22},
                ],
            },
            password="Senha10Abc",
        )
        self.assertEqual(payload["nicho"], "Automotivo")
        self.assertEqual(payload["data_abertura"], "10/05/2013")
        self.assertEqual(payload["quantidade_socios"], "1")
        self.assertEqual(payload["nome_fantasia"], "BRASIL AUTO PECAS")
        self.assertEqual(payload["responsavel_legal"], "JOAO LIMA")
        self.assertEqual(payload["socio_1"], "JOAO LIMA")
        self.assertEqual(payload["cpf_socio_1"], "12345678901")
        self.assertNotIn("socio_2", payload)
        self.assertEqual(payload["email"], "contato@brasilautopecas.com.br")
        self.assertEqual(payload["email_socio_1"], payload["email"])
        self.assertEqual(payload["email_login_gestor"], payload["email"])
        self.assertEqual(payload["email_confirmacao_admin"], payload["email"])
        self.assertEqual(payload["email_cobranca"], payload["email"])
        self.assertEqual(payload["senha_acesso"], "Senha10Abc")

    def test_empresario_individual_vira_um_responsavel(self):
        payload = registration_from_cnpj_payload(
            {
                "nome_fantasia": "BRASIL AUTO PECAS",
                "razao_social": "JOSE VALTON DO NASCIMENTO",
                "natureza_juridica": "Empresário (Individual)",
                "data_inicio_atividade": "2013-05-10",
                "cnae_fiscal": 4530703,
                "cnae_fiscal_descricao": "Comércio a varejo de peças e acessórios novos para veículos automotores",
                "qsa": [],
                "email": None,
            },
            password="Senha10Abc",
        )
        self.assertEqual(payload["empresa"], "BRASIL AUTO PECAS")
        self.assertEqual(payload["nicho"], "Automotivo")
        self.assertEqual(payload["quantidade_socios"], "1")
        self.assertEqual(payload["socio_1"], "JOSE VALTON DO NASCIMENTO")
        self.assertEqual(payload["email_login_gestor"], "")
        self.assertEqual(payload["senha_acesso"], "Senha10Abc")

    def test_sem_email_nao_inventa_email(self):
        payload = registration_from_cnpj_payload(
            {
                "razao_social": "EMPRESA SEM EMAIL LTDA",
                "cnae_fiscal": 5611201,
                "cnae_fiscal_descricao": "Restaurantes e similares",
                "qsa": [{"nome_socio": "ANA COSTA", "codigo_qualificacao_socio": 49}],
            },
            password=None,
        )
        self.assertEqual(payload["nicho"], "Alimentação")
        self.assertEqual(payload["quantidade_socios"], "1")
        self.assertEqual(payload["socio_1"], "ANA COSTA")
        self.assertEqual(payload["email_login_gestor"], "")
        self.assertTrue(payload["senha_acesso"])


if __name__ == "__main__":
    unittest.main()
