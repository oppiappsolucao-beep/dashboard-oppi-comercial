"""Cadastro novo exige serviço contratado completo."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.closed_services import require_registration_closed_services


class ClosedServicesRequiredTest(unittest.TestCase):
    def test_cadastro_sem_servico_nao_passa(self):
        with self.assertRaises(ValueError):
            require_registration_closed_services(
                [{"servico": "", "valor": "", "quantidade": "1", "forma_pagamento": "Mensal"}]
            )

    def test_valor_zerado_nao_passa(self):
        with self.assertRaises(ValueError) as error:
            require_registration_closed_services(
                [{"servico": "Oppi Ponto", "valor": "R$ 0,00", "quantidade": "1", "forma_pagamento": "Mensal"}]
            )
        self.assertIn("valor", str(error.exception).lower())

    def test_servico_completo_passa(self):
        require_registration_closed_services(
            [{"servico": "Oppi Ponto", "valor": "R$ 197,00", "quantidade": "2", "forma_pagamento": "Mensal"}]
        )


if __name__ == "__main__":
    unittest.main()
