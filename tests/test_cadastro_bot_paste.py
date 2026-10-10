"""Texto colado preenche nicho, abertura, fantasia e responsável legal."""
from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.cadastro_bot import read_dados_gerais


class CadastroBotPasteTest(unittest.TestCase):
    def test_cola_receita_preenche_nicho_abertura_e_responsavel(self):
        text = """
        Razão social: ACME COMERCIO LTDA
        Nome fantasia: ACME PECAS
        CNPJ: 18.088.253/0001-24
        Data de abertura: 10/05/2013
        Responsável: SILVIA LETICIA REIS DA SILVA BOMFIM
        E-mail: silvia@example.com
        Atividade econômica principal: 45.30-7-03 - Comércio a varejo de peças e acessórios novos para veículos automotores
        """
        result = read_dados_gerais(text, niche_options=["Automotivo", "Comércio e Varejo", "Outros"])
        fields = result["fields"]
        self.assertEqual(fields["empresa"], "ACME COMERCIO LTDA")
        self.assertEqual(fields["nome_fantasia"], "ACME PECAS")
        self.assertEqual(fields["data_abertura"], "10/05/2013")
        self.assertEqual(fields["nicho"], "Automotivo")
        self.assertEqual(fields["responsavel_legal"], "SILVIA LETICIA REIS DA SILVA BOMFIM")
        self.assertEqual(fields["socio_1"], "SILVIA LETICIA REIS DA SILVA BOMFIM")
        self.assertEqual(fields["data_fechamento"], date.today().isoformat())
        self.assertEqual(fields["email_login_gestor"], "silvia@example.com")
        self.assertEqual(fields["email_confirmacao_admin"], "silvia@example.com")
        self.assertEqual(fields["email_cobranca"], "silvia@example.com")
        self.assertTrue(fields["senha_acesso"])

    def test_cartao_cnpj_separa_empresa_fantasia_whatsapp_e_senha(self):
        text = """
        NOME EMPRESARIAL
        CLEAR SERVICOS JURIDICOS LTDA
        TÍTULO DO ESTABELECIMENTO (NOME DE FANTASIA)
        CLEAR JURIDICO
        TELEFONE
        (11) 3333-4444
        Celular / WhatsApp
        11988887777
        Senha
        Ab12cd34
        """
        fields = read_dados_gerais(text)["fields"]
        self.assertEqual(fields["empresa"], "CLEAR SERVICOS JURIDICOS LTDA")
        self.assertEqual(fields["nome_fantasia"], "CLEAR JURIDICO")
        self.assertEqual(fields["telefone_b2b"], "(11) 98888-7777")
        self.assertEqual(fields["telefone_fixo"], "(11) 3333-4444")
        self.assertEqual(fields["senha_acesso"], "Ab12cd34")

    def test_fantasia_mascarada_nao_vira_asterisco(self):
        text = """
        NOME EMPRESARIAL
        CLEAR SERVICOS JURIDICOS LTDA
        TÍTULO DO ESTABELECIMENTO (NOME DE FANTASIA)
        ********
        """
        fields = read_dados_gerais(text)["fields"]
        self.assertEqual(fields["empresa"], "CLEAR SERVICOS JURIDICOS LTDA")
        self.assertNotIn("nome_fantasia", fields)


if __name__ == "__main__":
    unittest.main()
