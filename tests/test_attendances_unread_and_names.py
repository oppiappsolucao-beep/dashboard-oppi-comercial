"""Testes locais — contador unread + finalizar todas + nome WhatsApp."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# SQLite isolado ANTES de importar connection/storage
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_tmp.name).as_posix()}"
os.environ["EVOLUTION_INSTANCE"] = "linha-teste,outra-linha"
os.environ.setdefault("APP_USERNAME", "tester")
os.environ.setdefault("APP_PASSWORD", "tester")

from database.connection import Base, SessionLocal, engine  # noqa: E402
from database import models  # noqa: E402, F401
from app.services import attendances_storage as store  # noqa: E402
from app.services import attendance_crm  # noqa: E402
from app.services import attendances as attendances_service  # noqa: E402


class AttendancesUnreadAndNamesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(bind=engine)

    def setUp(self):
        db = SessionLocal()
        try:
            db.query(models.AttendanceMessage).delete()
            db.query(models.AttendanceConversation).delete()
            db.commit()
        finally:
            db.close()

    def test_count_unread_ignores_finalized_and_zeros_on_finalize_all(self):
        c1 = store.upsert_conversation_by_phone(
            "5511942157917",
            contact_name="Cliente A",
            evolution_instance="linha-teste",
            status=store.STATUS_EM_ATENDIMENTO,
            ignore_suppression=True,
        )
        c2 = store.upsert_conversation_by_phone(
            "5511920679171",
            contact_name="Cliente B",
            evolution_instance="linha-teste",
            status=store.STATUS_NOVO_LEAD,
            ignore_suppression=True,
        )
        self.assertTrue(c1 and c2)
        store.update_conversation(c1["id"], unread_count=500)
        store.update_conversation(c2["id"], unread_count=55)

        self.assertEqual(store.count_unread(evolution_instance="linha-teste"), 555)

        # Finalizar só uma: deve zerar unread dela
        store.update_conversation(
            c1["id"],
            status=store.STATUS_FINALIZADO,
            ai_mode=store.AI_MODE_OFF,
            unread_count=0,
        )
        self.assertEqual(store.count_unread(evolution_instance="linha-teste"), 55)

        # Finalizar todas da linha: zera fila e badge
        result = store.finalize_open_conversations(evolution_instance="linha-teste")
        self.assertGreaterEqual(result["finalized"], 1)
        self.assertEqual(store.count_unread(evolution_instance="linha-teste"), 0)

        # Finalizado com unread residual antigo não conta
        store.update_conversation(c1["id"], unread_count=999)
        self.assertEqual(store.count_unread(evolution_instance="linha-teste"), 0)

    def test_whatsapp_name_keeps_existing_good_name(self):
        first = store.upsert_conversation_by_phone(
            "5511999998888",
            contact_name="Vicente Lemos",
            evolution_instance="linha-teste",
            ignore_suppression=True,
        )
        self.assertEqual(first["contact_name"], "Vicente Lemos")

        second = store.upsert_conversation_by_phone(
            "5511999998888",
            contact_name="Oppi Tech",
            evolution_instance="linha-teste",
            ignore_suppression=True,
        )
        # Nome bom não deve ser sobrescrito por pushName/sync posterior
        self.assertEqual(second["contact_name"], "Vicente Lemos")

        blank = store.upsert_conversation_by_phone(
            "5511987654321",
            contact_name="WhatsApp",
            evolution_instance="linha-teste",
            ignore_suppression=True,
        )
        self.assertTrue(blank)
        named = store.upsert_conversation_by_phone(
            "5511987654321",
            contact_name="Maria Silva",
            evolution_instance="linha-teste",
            ignore_suppression=True,
        )
        self.assertEqual(named.get("contact_name"), "Maria Silva")

    def test_placeholder_helpers(self):
        self.assertTrue(attendance_crm._looks_like_whatsapp_placeholder("Lead WhatsApp (11) 99999-0000"))
        self.assertTrue(attendance_crm._looks_like_whatsapp_placeholder("WhatsApp 5511"))
        self.assertFalse(attendance_crm._looks_like_whatsapp_placeholder("Oppi Tech"))
        self.assertFalse(attendance_crm.should_adopt_contact_name("Vicente Lemos", "Oppi Tech"))
        self.assertTrue(attendance_crm.should_adopt_contact_name("", "Vicente Lemos"))
        self.assertTrue(attendance_crm.should_adopt_contact_name("Lead WhatsApp 11", "Vicente Lemos"))

    def test_crm_display_puts_company_below_contact(self):
        out = attendances_service.apply_crm_display_names_to_conversation(
            {"contact_name": "WA Name", "phone_e164": "5511999999999"},
            {"contato": "Maria", "empresa": "Oppi Tech"},
        )
        self.assertEqual(out["contact_name"], "Maria")
        self.assertEqual(out["empresa_name"], "Oppi Tech")


if __name__ == "__main__":
    unittest.main(verbosity=2)
