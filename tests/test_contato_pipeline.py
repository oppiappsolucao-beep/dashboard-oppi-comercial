"""Testes — pipeline Contato→Perdido e limpeza de auto-atividades."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class ContatoPipelineTest(unittest.TestCase):
    def test_pipeline_starts_at_contato(self):
        from config.crm_options import PIPELINE_STAGE_OPTIONS, PIPELINE_STAGE_SLA

        self.assertEqual(PIPELINE_STAGE_OPTIONS[0], "Contato")
        self.assertNotIn("Novo Lead", PIPELINE_STAGE_OPTIONS)
        self.assertEqual(PIPELINE_STAGE_SLA["Contato"]["label"], "No mesmo dia")
        self.assertEqual(PIPELINE_STAGE_SLA["Qualificação"]["label"], "1 a 3 dias")
        self.assertEqual(PIPELINE_STAGE_SLA["Reunião"]["label"], "Até 7 dias")
        self.assertEqual(PIPELINE_STAGE_SLA["Proposta"]["label"], "Até 24 horas")
        self.assertEqual(PIPELINE_STAGE_SLA["Retorno"]["label"], "2 dias")
        self.assertEqual(PIPELINE_STAGE_SLA["Negociação"]["label"], "3 a 7 dias")
        self.assertEqual(PIPELINE_STAGE_SLA["Fechado"]["label"], "Processo concluído")
        self.assertEqual(PIPELINE_STAGE_SLA["Perdido"]["label"], "Oportunidade perdida")

    def test_novo_lead_maps_to_contato(self):
        from app.services.crm_validation_service import normalize_legacy_stage

        self.assertEqual(normalize_legacy_stage("Novo Lead"), "Contato")
        self.assertEqual(normalize_legacy_stage("Contato"), "Contato")
        self.assertEqual(normalize_legacy_stage("Proposta"), "Proposta")

    def test_sync_auto_is_noop(self):
        import pandas as pd
        from app.services.activity_service import sync_auto_activities

        sync_auto_activities(pd.DataFrame(), {}, "default")


if __name__ == "__main__":
    unittest.main(verbosity=2)
