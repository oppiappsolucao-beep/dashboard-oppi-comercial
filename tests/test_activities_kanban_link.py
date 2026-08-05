"""Testes — kanban prefere atividade manual do cadastro."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class KanbanRepresentativeTest(unittest.TestCase):
    def test_manual_activity_beats_auto(self):
        from app.services.activity_service import _pick_kanban_representative

        auto = {
            "id": "auto_12_lead_novo_sem_contato",
            "sheet_row": 12,
            "empresa": "Cliente X",
            "process_action": "Fazer primeiro contato",
            "status": "pendente",
            "updated_at": "2026-08-05T10:00:00",
            "sla_key": "atrasado",
        }
        manual = {
            "id": "act_abcdef123456",
            "sheet_row": 12,
            "empresa": "Cliente X",
            "process_action": "Retornar contato",
            "status": "pendente",
            "updated_at": "2026-08-05T09:00:00",  # mais antiga, mas manual
            "sla_key": "vence_hoje",
        }
        picked = _pick_kanban_representative([auto, manual])
        self.assertEqual(picked["id"], "act_abcdef123456")
        self.assertEqual(picked["process_action"], "Retornar contato")

    def test_close_auto_on_manual_create(self):
        from app.services import activity_service

        calls = []

        def fake_list(_tenant):
            return [
                {
                    "id": "auto_9_foo",
                    "sheet_row": 9,
                    "status": "pendente",
                },
                {
                    "id": "act_keep",
                    "sheet_row": 9,
                    "status": "pendente",
                },
            ]

        def fake_save(tenant, activity_id, payload, sync_pipeline=True):
            calls.append((activity_id, payload.get("status"), payload.get("result")))
            return {"id": activity_id, **payload}

        with patch.object(activity_service, "list_activities", fake_list), patch.object(
            activity_service, "save_activity", fake_save
        ):
            closed = activity_service._close_auto_activities_for_lead(
                "default", 9, keep_activity_id="act_keep", user="tester"
            )
        self.assertEqual(closed, 1)
        self.assertEqual(calls[0][0], "auto_9_foo")
        self.assertEqual(calls[0][1], "concluida")


if __name__ == "__main__":
    unittest.main(verbosity=2)
