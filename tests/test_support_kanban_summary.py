"""Cards do Kanban de Suporte nível 1 e 2."""
from app.services.kanban_summary import resolve_support_level, support_level, support_summary_cards


def test_so_suporte_nivel_1_e_2_entram_na_personalizacao():
    assert support_level("Suporte Nível 1") == 1
    assert support_level("Suporte nivel 2") == 2
    assert support_level("Suporte 1") == 1
    assert support_level("Suporte") is None
    assert support_level("Comercial") is None
    assert resolve_support_level("Suporte", "Suporte Nível 1") == 1
    assert resolve_support_level("Suporte", "Suporte nivel 2") == 2
    assert resolve_support_level("Comercial", "Suporte Nível 1") is None


def test_cinco_cards_da_fila_de_suporte():
    orders = [
        {"queue_id": "analise", "status": "aberta", "subject": "Desbloqueio", "empresa": "Oficina Sol", "created_at": "2026-10-02", "scheduled_date": "2026-10-02"},
        {"queue_id": "analise", "status": "aberta", "subject": "desbloqueio", "empresa": "Oficina Sol", "created_at": "2026-10-03", "scheduled_date": "2026-10-03"},
        {"queue_id": "fila-andamento", "status": "em_andamento", "subject": "Senha", "empresa": "Clínica Sul", "scheduled_date": "2026-10-04", "created_at": "2026-10-04", "updated_at": "2026-10-04"},
        {"queue_id": "concluida", "status": "concluida", "subject": "Desbloqueio", "empresa": "Oficina Sol", "created_at": "2026-10-01", "updated_at": "2026-10-05", "scheduled_date": "2026-10-01"},
        {"queue_id": "analise", "status": "cancelada", "subject": "Desbloqueio", "empresa": "Oficina Sol", "created_at": "2026-10-02", "scheduled_date": "2026-10-02"},
    ]
    cards = support_summary_cards(orders, "2026-10-01", "2026-10-06", level=1, andamento_ids={"fila-andamento"})
    assert len(cards) == 5
    assert cards[0]["label"] == "Análise"
    assert cards[0]["value"] == 2
    assert cards[1]["label"] == "Andamento"
    assert cards[1]["value"] == 1
    assert cards[2]["label"] == "Concluídos"
    assert cards[2]["value"] == 1
    assert cards[3]["label"] == "Desbloqueio"
    assert cards[3]["value"] == 3
    assert cards[4]["label"] == "Oficina Sol"
    assert cards[4]["value"] == 3
