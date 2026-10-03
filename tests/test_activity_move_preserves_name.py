"""Testes — merge ao salvar atividade (mover etapa não apaga nome)."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_tmp.name).as_posix()}"
os.environ.setdefault("APP_USERNAME", "tester")
os.environ.setdefault("APP_PASSWORD", "tester")

from database.connection import Base, SessionLocal, engine  # noqa: E402
from database import models  # noqa: E402, F401
from app.services import crm_aux_storage as aux  # noqa: E402
from app.services.activity_service import _resolve_effective_stage, mover_atividade_kanban  # noqa: E402


class SaveActivityMergeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(bind=engine)

    def setUp(self):
        db = SessionLocal()
        try:
            db.query(models.CrmActivity).delete()
            db.commit()
        finally:
            db.close()

    def test_partial_update_keeps_empresa_and_sheet_row(self):
        saved = aux.save_activity_pg(
            "default",
            "act_move_test",
            {
                "empresa": "Oppi Tech",
                "contato": "Oppi Tech",
                "sheet_row": 42,
                "stage": "Contato",
                "process_action": "Realizar contato",
                "status": "pendente",
            },
        )
        self.assertEqual(saved["empresa"], "Oppi Tech")
        self.assertEqual(saved["sheet_row"], 42)

        updated = aux.save_activity_pg(
            "default",
            "act_move_test",
            {"stage": "Qualificação", "move_stage": "Qualificação"},
        )
        self.assertEqual(updated["empresa"], "Oppi Tech")
        self.assertEqual(updated["contato"], "Oppi Tech")
        self.assertEqual(updated["sheet_row"], 42)
        self.assertEqual(updated["stage"], "Qualificação")

        loaded = aux.get_activity_pg("default", "act_move_test")
        self.assertEqual(loaded["empresa"], "Oppi Tech")
        self.assertEqual(loaded["stage"], "Qualificação")

    def test_effective_stage_prefers_activity_stage(self):
        record = {"stage": "Qualificação", "sheet_row": 1, "tenant_id": "default"}
        with patch("app.services.activity_service.get_lead_action", return_value={"stage_override": "Contato"}):
            self.assertEqual(_resolve_effective_stage(record, "default"), "Qualificação")


if __name__ == "__main__":
    unittest.main(verbosity=2)
