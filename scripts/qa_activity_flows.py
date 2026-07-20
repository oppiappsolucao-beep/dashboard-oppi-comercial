"""QA automatizado dos fluxos de Atividades (sem UI/login).

Execute: py scripts/qa_activity_flows.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app.services.activities_storage as activities_storage
import app.services.lead_actions_storage as lead_actions_storage
import app.services.activity_service as activity_service
from app.services.activity_service import (
    ActivitiesViewParams,
    atualizar_proxima_acao_atividade,
    build_activities_kanban,
    build_activity_detail_panel,
    build_activity_timeline_for_activity,
    buscar_atividades,
    criar_atividade,
    mover_atividade_kanban,
    atualizar_atividade_inline,
)
from app.services.activities_storage import DEFAULT_TENANT_ID, save_activity
import pandas as pd


class QAResult:
    def __init__(self) -> None:
        self.passed: list[str] = []
        self.failed: list[str] = []
        self.warnings: list[str] = []

    def ok(self, msg: str) -> None:
        self.passed.append(msg)

    def fail(self, msg: str) -> None:
        self.failed.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    def report(self) -> int:
        print("\n=== QA Atividades ===\n")
        for item in self.passed:
            print(f"  OK   {item}")
        for item in self.warnings:
            print(f"  WARN {item}")
        for item in self.failed:
            print(f"  FAIL {item}")
        print(f"\nResumo: {len(self.passed)} ok, {len(self.warnings)} avisos, {len(self.failed)} falhas\n")
        return 1 if self.failed else 0


def _empty_df(sheet_row: int, empresa: str) -> tuple[pd.DataFrame, dict]:
    df = pd.DataFrame([{
        "_sheet_row": sheet_row,
        "_empresa": empresa,
        "_vendedor": "QA Tester",
        "_status_grupo": "Novo Lead",
        "_status_original": "Novo Lead",
        "_data_chamado": datetime.now(),
        "_ultima_atualizacao": datetime.now(),
    }])
    columns = {
        "empresa": "_empresa",
        "vendedor": "_vendedor",
        "status": "_status_grupo",
        "data_chamado": "_data_chamado",
        "ultima_atualizacao": "_ultima_atualizacao",
        "socio_1": None,
    }
    return df, columns


def _kanban_column_for_empresa(kanban: list[dict], empresa: str) -> str | None:
    for column in kanban:
        for card in column["cards"]:
            if card.get("empresa") == empresa:
                return column["stage"]
    return None


def _count_cards_for_empresa(kanban: list[dict], empresa: str) -> int:
    total = 0
    for column in kanban:
        for card in column["cards"]:
            if card.get("empresa") == empresa:
                total += 1
    return total


def run_qa() -> QAResult:
    result = QAResult()
    tmp = Path(tempfile.mkdtemp(prefix="oppi_qa_"))
    activities_storage.STORAGE_PATH = tmp / "activities.json"
    lead_actions_storage.STORAGE_PATH = tmp / "lead_actions.json"

    tenant = DEFAULT_TENANT_ID
    user = "qa_tester"
    empresa = "EMPRESA FAKE QA LTDA"
    sheet_row = 99901
    df, columns = _empty_df(sheet_row, empresa)
    params = ActivitiesViewParams()

    try:
        # 1) Cadastro de atividade (simula novo lead com primeira atividade)
        payload = {
            "sheet_row": sheet_row,
            "empresa": empresa,
            "contato": "Contato QA",
            "stage": "Novo Lead",
            "activity_type": "Contato",
            "process_action": "Fazer primeiro contato",
            "channel": "WhatsApp",
            "assigned_user_id": "QA Tester",
            "scheduled_date": date.today().isoformat(),
            "scheduled_time": "10:00",
            "status": "pendente",
            "priority": "Média",
            "description": "Lead fake para QA",
        }
        created, err = criar_atividade(tenant, payload, user, is_admin_user=True)
        if err or not created:
            result.fail(f"Criar atividade fake: {err or 'sem retorno'}")
            return result
        activity_id = created["id"]
        result.ok(f"Atividade criada ({activity_id})")

        activities = buscar_atividades(df, columns, tenant)
        kanban = build_activities_kanban(activities, params, tenant)
        col = _kanban_column_for_empresa(kanban, empresa)
        if col != "Novo Lead":
            result.fail(f"Kanban inicial deveria estar em Novo Lead, veio: {col}")
        else:
            result.ok("Kanban mostra lead em Novo Lead após cadastro")

        if _count_cards_for_empresa(kanban, empresa) != 1:
            result.fail(f"Kanban duplicou card no cadastro ({_count_cards_for_empresa(kanban, empresa)} cards)")
        else:
            result.ok("Sem duplicação no Kanban após cadastro")

        # 2) Mudar próxima ação -> deve mover coluna (Agendar reunião -> Qualificação)
        normalized, err = atualizar_proxima_acao_atividade(tenant, activity_id, "Agendar reunião", user)
        if err:
            result.fail(f"Atualizar próxima ação: {err}")
        else:
            result.ok(f"Próxima ação alterada para: {normalized}")

        activities = buscar_atividades(df, columns, tenant)
        kanban = build_activities_kanban(activities, params, tenant)
        col = _kanban_column_for_empresa(kanban, empresa)
        if col != "Qualificação":
            result.fail(f"Após próxima ação, coluna deveria ser Qualificação, veio: {col}")
        else:
            result.ok("Kanban moveu para Qualificação ao alterar próxima ação")

        if _count_cards_for_empresa(kanban, empresa) != 1:
            result.fail(f"Duplicação após mudar próxima ação ({_count_cards_for_empresa(kanban, empresa)} cards)")
        else:
            result.ok("Sem duplicação após mudar próxima ação")

        timeline = build_activity_timeline_for_activity(tenant, activity_id, df, columns)
        labels = [step["label"] for step in timeline]
        if not any("Próxima ação alterada" in label for label in labels):
            result.fail("Timeline não registrou alteração de próxima ação")
        else:
            result.ok("Timeline registrou alteração de próxima ação")

        for step in timeline:
            if "00:00" in step.get("at", "") and "Lead entrou" in step.get("label", ""):
                result.warn("Lead entrou ainda pode exibir 00:00 se data vier sem hora da planilha")
                break

        # 3) Arrastar etapa (mover para Contato)
        moved, err = mover_atividade_kanban(tenant, activity_id, "Contato", user)
        if err:
            result.fail(f"Mover etapa no Kanban: {err}")
        else:
            result.ok("Mover etapa para Contato")

        activities = buscar_atividades(df, columns, tenant)
        kanban = build_activities_kanban(activities, params, tenant)
        col = _kanban_column_for_empresa(kanban, empresa)
        if col != "Contato":
            result.fail(f"Após mover etapa, coluna deveria ser Contato, veio: {col}")
        else:
            result.ok("Kanban persistiu coluna Contato após mover etapa")

        # 4) Concluir em Fechado sem duplicar
        fechado_payload = {
            "sheet_row": sheet_row,
            "empresa": empresa,
            "contato": "Contato QA",
            "stage": "Fechado",
            "activity_type": "Contato",
            "process_action": "Confirmar fechamento",
            "channel": "WhatsApp",
            "assigned_user_id": "QA Tester",
            "scheduled_date": date.today().isoformat(),
            "scheduled_time": "11:00",
            "status": "pendente",
            "priority": "Média",
        }
        fechado, err = criar_atividade(tenant, fechado_payload, user, is_admin_user=True)
        if err or not fechado:
            result.fail(f"Criar atividade em Fechado: {err}")
        else:
            fechado_id = fechado["id"]
            updated, err = atualizar_atividade_inline(
                tenant,
                fechado_id,
                {"status": "concluida", "result": "Avançou de etapa", "channel": "WhatsApp", "assigned_user_id": "QA Tester"},
                user,
            )
            if err:
                result.fail(f"Concluir em Fechado: {err}")
            else:
                result.ok("Concluir atividade em Fechado")

            activities = buscar_atividades(df, columns, tenant)
            kanban = build_activities_kanban(activities, params, tenant)
            fechado_cards = _count_cards_for_empresa(kanban, empresa)
            # Empresa fake tem 2 atividades (qualificação/contato + fechado) mas dedupe por lead = 1 card
            if fechado_cards != 1:
                result.fail(f"Duplicação ao concluir Fechado: {fechado_cards} cards para mesma empresa")
            else:
                result.ok("Sem duplicação no Kanban ao concluir em Fechado")

            timeline_f = build_activity_timeline_for_activity(tenant, fechado_id, df, columns)
            if not any("conclu" in step.get("label", "").lower() for step in timeline_f):
                result.fail("Timeline não registrou conclusão em Fechado")
            else:
                result.ok("Timeline registrou conclusão em Fechado")

        # 5) Comparar datetimes (regressão do erro offset-naive/aware)
        try:
            activity_service.calcular_sla_atividade(
                {"scheduled_at": datetime.now().isoformat(timespec="seconds"), "stage": "Contato", "priority": 20},
                "pendente",
            )
            activity_service._now()
            buscar_atividades(df, columns, tenant)
            result.ok("Comparações de datetime sem erro naive/aware")
        except TypeError as exc:
            result.fail(f"Erro datetime naive/aware: {exc}")

        # 6) Simular reload (buscar de novo do storage)
        activities_reload = buscar_atividades(df, columns, tenant)
        kanban_reload = build_activities_kanban(activities_reload, params, tenant)
        col_reload = _kanban_column_for_empresa(kanban_reload, empresa)
        if col_reload != "Contato":
            result.fail(f"Após reload simulado, coluna deveria ser Contato, veio: {col_reload}")
        else:
            result.ok("Etapa persiste após reload simulado do storage")

        # 7) Abrir cadastro — lead com sheet_row abre edição
        panel = build_activity_detail_panel(tenant, activity_id, df, columns)
        if not panel:
            result.fail("Painel de detalhe não retornou dados")
        else:
            href = panel.get("lead_href", "")
            expected = f"/cadastro/todos/{sheet_row}/editar"
            if href != expected:
                result.fail(f"Abrir cadastro deveria ir para {expected}, veio: {href}")
            else:
                result.ok("Abrir cadastro aponta para edição do cadastro na planilha")

        # 8) Abrir cadastro — lead sem sheet_row abre novo cadastro pré-preenchido
        orphan = save_activity(tenant, None, {
            "empresa": "LEAD ORFAO QA",
            "contato": "Contato QA",
            "stage": "Qualificação",
            "title": "Qualificar lead",
            "process_action": "Qualificar lead",
            "channel": "WhatsApp",
            "assigned_user_id": "QA Tester",
            "scheduled_date": date.today().isoformat(),
            "scheduled_time": "08:00",
            "status": "pendente",
            "priority": 20,
            "description": "Lead sem planilha",
            "next_action": "Agendar reunião",
        })
        orphan_panel = build_activity_detail_panel(tenant, orphan["id"], df, columns)
        orphan_href = orphan_panel.get("lead_href", "") if orphan_panel else ""
        if not orphan_href.startswith("/cadastro/novo?"):
            result.fail(f"Lead sem planilha deveria abrir novo cadastro, veio: {orphan_href}")
        elif "empresa=LEAD+ORFAO+QA" not in orphan_href and "empresa=LEAD%20ORFAO%20QA" not in orphan_href:
            result.fail(f"Novo cadastro não pré-preencheu empresa: {orphan_href}")
        elif "activity_channel=WhatsApp" not in orphan_href:
            result.fail(f"Novo cadastro não pré-preencheu canal: {orphan_href}")
        else:
            result.ok("Lead sem planilha abre novo cadastro pré-preenchido")

        # 9) Resolver sheet_row pela empresa quando ausente na atividade
        df_resolve, columns_resolve = _empty_df(88801, "EMPRESA RESOLVIDA QA")
        resolved = save_activity(tenant, None, {
            "empresa": "EMPRESA RESOLVIDA QA",
            "contato": "Contato",
            "stage": "Novo Lead",
            "title": "Fazer primeiro contato",
            "process_action": "Fazer primeiro contato",
            "channel": "WhatsApp",
            "assigned_user_id": "QA Tester",
            "scheduled_date": date.today().isoformat(),
            "scheduled_time": "09:00",
            "status": "pendente",
        })
        resolved_panel = build_activity_detail_panel(tenant, resolved["id"], df_resolve, columns_resolve)
        panel_href = resolved_panel.get("lead_href", "") if resolved_panel else ""
        expected_resolve = "/cadastro/todos/88801/editar"
        if panel_href != expected_resolve:
            result.fail(f"Resolver sheet_row pela empresa falhou: {panel_href}")
        else:
            result.ok("Sheet_row resolvido pela empresa abre edição do cadastro")

        # 10) SLA por etapa — Novo Lead dentro de 1h = no prazo
        now = activity_service._now()
        sla_ok, _, _ = activity_service.calcular_sla_atividade(
            {
                "stage": "Novo Lead",
                "stage_entered_at": (now - timedelta(minutes=30)).isoformat(timespec="seconds"),
                "priority": 20,
            },
            "pendente",
            now=now,
        )
        if sla_ok != "no_prazo":
            result.fail(f"Novo Lead 30min deveria ser no_prazo, veio: {sla_ok}")
        else:
            result.ok("SLA Novo Lead: no prazo dentro de 1 hora")

        sla_late, label_late, _ = activity_service.calcular_sla_atividade(
            {
                "stage": "Novo Lead",
                "stage_entered_at": (now - timedelta(hours=2)).isoformat(timespec="seconds"),
                "priority": 20,
            },
            "pendente",
            now=now,
        )
        if sla_late != "atrasado":
            result.fail(f"Novo Lead 2h deveria ser atrasado, veio: {sla_late} ({label_late})")
        else:
            result.ok("SLA Novo Lead: atrasado após 1 hora")

        sla_contato, _, _ = activity_service.calcular_sla_atividade(
            {
                "stage": "Contato",
                "stage_entered_at": now.replace(hour=9, minute=0).isoformat(timespec="seconds"),
                "priority": 20,
            },
            "pendente",
            now=now.replace(hour=10, minute=0),
        )
        if sla_contato != "no_prazo":
            result.fail(f"Contato no mesmo dia de manhã deveria ser no_prazo, veio: {sla_contato}")
        else:
            result.ok("SLA Contato: no prazo no mesmo dia")

        sla_contato_late, _, _ = activity_service.calcular_sla_atividade(
            {
                "stage": "Contato",
                "stage_entered_at": (now - timedelta(days=1)).isoformat(timespec="seconds"),
                "priority": 20,
            },
            "pendente",
            now=now,
        )
        if sla_contato_late != "atrasado":
            result.fail(f"Contato de ontem deveria ser atrasado, veio: {sla_contato_late}")
        else:
            result.ok("SLA Contato: atrasado após o dia")

        sla_reuniao_warn, _, _ = activity_service.calcular_sla_atividade(
            {
                "stage": "Reunião",
                "stage_entered_at": (now - timedelta(days=7)).isoformat(timespec="seconds"),
                "priority": 20,
            },
            "pendente",
            now=now,
        )
        if sla_reuniao_warn != "vence_hoje":
            result.fail(f"Reunião no 7º dia deveria ser vence_hoje, veio: {sla_reuniao_warn}")
        else:
            result.ok("SLA Reunião: vence hoje no último dia")

        sla_fechado, label_fechado, _ = activity_service.calcular_sla_atividade(
            {"stage": "Fechado", "priority": 20},
            "pendente",
            now=now,
        )
        if sla_fechado != "concluido":
            result.fail(f"Fechado deveria ser concluído, veio: {sla_fechado} ({label_fechado})")
        else:
            result.ok("SLA Fechado: processo concluído")

        # 11) Mover card reinicia SLA na nova etapa
        moved, err = mover_atividade_kanban(tenant, activity_id, "Qualificação", user)
        if err:
            result.fail(f"Mover para Qualificação (SLA): {err}")
        else:
            sla_after_move = moved.get("sla_key")
            if sla_after_move != "no_prazo":
                result.fail(f"Após mover etapa, SLA deveria reiniciar (no_prazo), veio: {sla_after_move}")
            else:
                result.ok("SLA reinicia ao mover card de etapa")

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    return result


if __name__ == "__main__":
    qa = run_qa()
    raise SystemExit(qa.report())
