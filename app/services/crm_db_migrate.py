"""Schema + migração one-shot CRM → Postgres (SoT) + backfill attendance."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import inspect, text

from database.connection import SessionLocal, engine
from database.models import (
    AppMeta,
    AttendanceConversation,
    CrmAccountUser,
    CrmActivity,
    CrmAppSetting,
    CrmMonthlyGoal,
    CrmProposal,
    CrmRegistration,
)

from app.services.legacy_core import normalize_text
from app.services.storage_paths import get_storage_dir

logger = logging.getLogger(__name__)

MIGRATION_KEY = "crm_postgres_migrated"
_TZ = ZoneInfo("America/Sao_Paulo")

CRM_TABLE_MODELS = (
    CrmRegistration,
    CrmActivity,
    CrmProposal,
    CrmAppSetting,
    CrmAccountUser,
    CrmMonthlyGoal,
)


def _now_iso() -> str:
    return datetime.now(_TZ).replace(tzinfo=None).isoformat(timespec="seconds")


def _json_dumps(value) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, default=str)


def _sql_type_for_column(column) -> str:
    """DDL simplificado para ALTER TABLE ADD COLUMN (Postgres/SQLite)."""
    from sqlalchemy import Boolean, Float, Integer, String, Text

    col_type = column.type
    if isinstance(col_type, Boolean):
        return "BOOLEAN DEFAULT FALSE"
    if isinstance(col_type, Integer):
        return "INTEGER"
    if isinstance(col_type, Float):
        return "FLOAT"
    if isinstance(col_type, Text):
        return "TEXT DEFAULT ''"
    if isinstance(col_type, String):
        length = int(getattr(col_type, "length", None) or 255)
        return f"VARCHAR({length}) DEFAULT ''"
    return "TEXT DEFAULT ''"


def _widen_crm_registration_string_columns() -> None:
    """Amplia VARCHARs curtos já criados (create checkfirst não altera tipo)."""
    from sqlalchemy import String

    try:
        insp = inspect(engine)
        if "crm_registrations" not in insp.get_table_names():
            return
        existing = {c["name"]: c for c in insp.get_columns("crm_registrations")}
        statements: list[str] = []
        dialect = engine.dialect.name
        for column in CrmRegistration.__table__.columns:
            if not isinstance(column.type, String):
                continue
            want = int(getattr(column.type, "length", None) or 0)
            if want <= 0:
                continue
            meta = existing.get(column.name)
            if not meta:
                continue
            current_type = str(meta.get("type") or "")
            # Ex.: VARCHAR(20) / character varying(20)
            import re

            match = re.search(r"(\d+)", current_type)
            current_len = int(match.group(1)) if match else 0
            if current_len <= 0 or current_len >= want:
                continue
            if dialect == "postgresql":
                statements.append(
                    f"ALTER TABLE crm_registrations ALTER COLUMN {column.name} "
                    f"TYPE VARCHAR({want})"
                )
            elif dialect == "sqlite":
                # SQLite ignora length — nada a fazer.
                continue
            else:
                statements.append(
                    f"ALTER TABLE crm_registrations MODIFY COLUMN {column.name} "
                    f"VARCHAR({want})"
                )
        if not statements:
            return
        with engine.begin() as conn:
            for stmt in statements:
                conn.execute(text(stmt))
        logger.info("Schema CRM widen: %s", "; ".join(statements))
    except Exception:
        logger.exception("Falha ao ampliar colunas crm_registrations")


def ensure_crm_schema() -> None:
    """Cria tabelas CRM, ALTERs de colunas faltantes e registration_id em attendance."""
    for model in CRM_TABLE_MODELS:
        try:
            model.__table__.create(bind=engine, checkfirst=True)
        except Exception:
            logger.exception("Falha ao criar tabela %s", model.__tablename__)

    # create(checkfirst=True) NÃO adiciona colunas novas — sincroniza aqui.
    try:
        insp = inspect(engine)
        table_names = set(insp.get_table_names())
        for model in CRM_TABLE_MODELS:
            table = model.__tablename__
            if table not in table_names:
                continue
            existing = {c["name"] for c in insp.get_columns(table)}
            statements: list[str] = []
            for column in model.__table__.columns:
                if column.name in existing:
                    continue
                ddl = _sql_type_for_column(column)
                statements.append(f"ALTER TABLE {table} ADD COLUMN {column.name} {ddl}")
            if not statements:
                continue
            with engine.begin() as conn:
                for stmt in statements:
                    conn.execute(text(stmt))
            logger.info("Schema CRM %s: %s", table, "; ".join(statements))
    except Exception:
        logger.exception("Falha ao sincronizar colunas CRM")

    _widen_crm_registration_string_columns()

    try:
        insp = inspect(engine)
        if "attendance_conversations" not in insp.get_table_names():
            return
        cols = {c["name"] for c in insp.get_columns("attendance_conversations")}
        if "registration_id" in cols:
            return
        with engine.begin() as conn:
            conn.execute(
                text(
                    "ALTER TABLE attendance_conversations "
                    "ADD COLUMN registration_id INTEGER"
                )
            )
        logger.info("Schema attendance: adicionada registration_id")
    except Exception:
        logger.exception("Falha ao adicionar registration_id em attendance_conversations")


def _registration_string_limits() -> dict[str, int]:
    from sqlalchemy import String

    limits: dict[str, int] = {}
    for column in CrmRegistration.__table__.columns:
        if isinstance(column.type, String):
            length = int(getattr(column.type, "length", None) or 0)
            if length > 0:
                limits[column.name] = length
    return limits


def _clip_registration_value(key: str, value: str, limits: dict[str, int]) -> str:
    text_val = normalize_text(value)
    max_len = limits.get(key)
    if max_len and len(text_val) > max_len:
        return text_val[:max_len]
    return text_val


def _load_json_file(name: str, default):
    path = get_storage_dir() / name
    if not path.exists():
        return default
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default
    return data if isinstance(data, type(default)) else default


def _import_registrations_from_sheet(
    *,
    dry_run: bool = False,
    force_refresh: bool = False,
) -> dict:
    from app.services.crm_registrations_storage import (
        DEFAULT_TENANT_ID,
        FIELD_TO_SHEET_HEADER,
        REGISTRATION_FIELD_KEYS,
    )
    from app.services.legacy_core import (
        identify_columns,
        load_sheet_data,
        normalize_text as nt,
    )

    result = {"imported": 0, "skipped": 0, "source": "sheet"}
    df = None

    # 1) Sempre preferir cache quente (evita 429 / worker frio no force_refresh).
    try:
        df = load_sheet_data(force_refresh=False)
    except Exception:
        df = None

    # 2) Snapshot em disco / prepared cache
    if df is None or getattr(df, "empty", True):
        try:
            from app.services.legacy_core import hydrate_sheet_cache_from_disk, get_cached_prepared_data

            hydrate_sheet_cache_from_disk()
            cached = get_cached_prepared_data()
            if cached is not None:
                prepared, _cols = cached
                if prepared is not None and not getattr(prepared, "empty", True):
                    df = prepared
                    result["source"] = "snapshot"
        except Exception:
            pass

    # 3) API Google com retries (429/cache frio no boot).
    if df is None or getattr(df, "empty", True):
        import time

        last_error = ""
        for attempt in range(3):
            try:
                df = load_sheet_data(force_refresh=True)
                if df is not None and not getattr(df, "empty", True):
                    result["source"] = "sheet_refresh"
                    result["api_attempts"] = attempt + 1
                    break
            except Exception as error:
                last_error = str(error)
                logger.exception(
                    "Falha ao forçar refresh da Folha1 na migração CRM (tentativa %s)",
                    attempt + 1,
                )
                df = None
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
        if last_error:
            result["api_error"] = last_error
    elif force_refresh:
        # Cache já tem dados — mantém; não arrisca esvaziar com 429.
        result["source"] = "sheet_cache"

    if df is None or getattr(df, "empty", True):
        result["source"] = "empty"
        return result

    result["sheet_rows"] = int(len(df))
    columns = identify_columns(df)
    if "_sheet_row" not in df.columns:
        df = df.copy()
        df["_sheet_row"] = range(2, len(df) + 2)

    # lead_actions merge (arquivo local — ainda antes do cutover Postgres)
    lead_actions = _load_lead_actions_legacy()
    if not lead_actions:
        try:
            from app.services.crm_local_db import load_lead_actions_store

            local_store = load_lead_actions_store() or {}
            bucket = local_store.get("default") if isinstance(local_store, dict) else {}
            if isinstance(bucket, dict):
                lead_actions = {
                    str(k): v for k, v in bucket.items() if isinstance(v, dict)
                }
        except Exception:
            pass

    if dry_run:
        result["imported"] = int(len(df))
        return result

    db = SessionLocal()
    limits = _registration_string_limits()
    try:
        existing = {
            int(r.sheet_row): r
            for r in db.query(CrmRegistration).all()
            if r.sheet_row
        }
        for _, series in df.iterrows():
            try:
                sheet_row = int(series.get("_sheet_row") or 0)
            except Exception:
                sheet_row = 0
            if sheet_row < 2:
                result["skipped"] += 1
                continue

            payload = {}
            for key in REGISTRATION_FIELD_KEYS:
                header = columns.get(key)
                if not header and key == "email_empresa":
                    header = columns.get("email")
                if not header and key == "status":
                    header = columns.get("status_whatsapp") or columns.get("status")
                if not header:
                    header = FIELD_TO_SHEET_HEADER.get(key)
                value = ""
                if header and header in series.index:
                    value = nt(series.get(header))
                if not value and f"_{key}" in series.index:
                    value = nt(series.get(f"_{key}"))
                if not value and key in series.index:
                    value = nt(series.get(key))
                payload[key] = value

            if not nt(payload.get("empresa")):
                result["skipped"] += 1
                continue

            actions = lead_actions.get(str(sheet_row)) or {}
            if not isinstance(actions, dict):
                actions = {}

            try:
                with db.begin_nested():
                    row = existing.get(sheet_row)
                    if row is None:
                        row = CrmRegistration(
                            tenant_id=DEFAULT_TENANT_ID,
                            sheet_row=sheet_row,
                            created_at=_now_iso(),
                        )
                        db.add(row)

                    overflow_notes: list[str] = []
                    for key in REGISTRATION_FIELD_KEYS:
                        raw_val = nt(payload.get(key))
                        clipped = _clip_registration_value(key, raw_val, limits)
                        if raw_val and clipped != raw_val:
                            overflow_notes.append(f"{key}: {raw_val}")
                        setattr(row, key, clipped)

                    tipo = nt(actions.get("cadastro_tipo")).lower()
                    row.cadastro_tipo = _clip_registration_value(
                        "cadastro_tipo",
                        "empresa" if tipo == "empresa" else "lead",
                        limits,
                    )
                    if "cadastro_ativo" in actions:
                        raw = actions.get("cadastro_ativo")
                        if isinstance(raw, bool):
                            row.cadastro_ativo = raw
                        else:
                            text_val = nt(raw).lower()
                            row.cadastro_ativo = text_val not in {
                                "0",
                                "false",
                                "nao",
                                "não",
                                "inativo",
                                "off",
                                "no",
                            }
                    row.nicho = _clip_registration_value(
                        "nicho", nt(actions.get("nicho")), limits
                    )
                    row.payment_history_json = _json_dumps(
                        actions.get("payment_history")
                        if isinstance(actions.get("payment_history"), list)
                        else []
                    )
                    row.closed_services_json = _json_dumps(
                        actions.get("closed_services")
                        if isinstance(actions.get("closed_services"), list)
                        else []
                    )
                    row.actions_json = _json_dumps(actions)
                    if overflow_notes:
                        note = " | ".join(overflow_notes)
                        current = nt(row.observacoes)
                        row.observacoes = (
                            f"{current}\n[import-trunc] {note}".strip()
                            if current
                            else f"[import-trunc] {note}"
                        )
                    row.updated_at = _now_iso()
                    db.flush()
                    existing[sheet_row] = row
                    result["imported"] += 1
            except Exception:
                result["skipped"] += 1
                result["row_errors"] = int(result.get("row_errors") or 0) + 1
                logger.exception(
                    "Falha importando sheet_row=%s; seguindo para próxima linha",
                    sheet_row,
                )

        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Falha importando crm_registrations")
        raise
    finally:
        db.close()
    return result


def _load_lead_actions_legacy() -> dict[str, dict]:
    data = _load_json_file("lead_actions.json", {})
    if not isinstance(data, dict):
        return {}
    bucket = data.get("default") or data.get("DEFAULT_TENANT_ID")
    if isinstance(bucket, dict):
        return {
            str(k): v
            for k, v in bucket.items()
            if isinstance(v, dict)
        }
    # flat map?
    if all(isinstance(v, dict) for v in data.values()):
        # maybe {sheet_row: record}
        sample_key = next(iter(data.keys()), "")
        if str(sample_key).isdigit():
            return {str(k): v for k, v in data.items() if isinstance(v, dict)}
    return {}


def _import_activities(*, dry_run: bool = False) -> int:
    store = _load_json_file("activities.json", {})
    if not isinstance(store, dict):
        return 0
    count = 0
    db = SessionLocal()
    try:
        for tenant, bucket in store.items():
            if not isinstance(bucket, dict):
                continue
            activities = bucket.get("activities") if "activities" in bucket else bucket
            if not isinstance(activities, dict):
                continue
            for activity_id, record in activities.items():
                if not isinstance(record, dict):
                    continue
                count += 1
                if dry_run:
                    continue
                existing = db.get(CrmActivity, str(activity_id))
                if existing is None:
                    existing = CrmActivity(id=str(activity_id))
                    db.add(existing)
                existing.tenant_id = normalize_text(record.get("tenant_id") or tenant) or "default"
                existing.sheet_row = int(record.get("sheet_row") or 0) or None
                existing.payload_json = _json_dumps(record)
                existing.deleted = bool(record.get("deleted"))
                existing.created_at = normalize_text(record.get("created_at")) or _now_iso()
                existing.updated_at = normalize_text(record.get("updated_at")) or _now_iso()
        if not dry_run:
            db.commit()
    except Exception:
        db.rollback()
        logger.exception("Falha importando crm_activities")
    finally:
        db.close()
    return count


def _import_proposals(*, dry_run: bool = False) -> int:
    rows = _load_json_file("proposal_history.json", [])
    if not isinstance(rows, list):
        return 0
    count = 0
    db = SessionLocal()
    try:
        for item in rows:
            if not isinstance(item, dict):
                continue
            entry_id = normalize_text(item.get("id"))
            if not entry_id:
                continue
            count += 1
            if dry_run:
                continue
            existing = db.get(CrmProposal, entry_id)
            if existing is None:
                existing = CrmProposal(id=entry_id)
                db.add(existing)
            existing.cliente = normalize_text(item.get("cliente"))
            existing.cnpj_cpf = normalize_text(item.get("cnpj_cpf"))
            existing.payload_json = _json_dumps(item)
            existing.created_at = normalize_text(item.get("created_at")) or _now_iso()
        if not dry_run:
            db.commit()
    except Exception:
        db.rollback()
        logger.exception("Falha importando crm_proposals")
    finally:
        db.close()
    return count


def _import_app_settings(*, dry_run: bool = False) -> int:
    data = _load_json_file("app_settings.json", {})
    if not isinstance(data, dict) or not data:
        return 0
    count = 0
    db = SessionLocal()
    try:
        for key, value in data.items():
            count += 1
            if dry_run:
                continue
            raw = value
            if not isinstance(raw, str):
                raw = json.dumps(raw, ensure_ascii=False, default=str)
            existing = db.get(CrmAppSetting, str(key))
            if existing is None:
                existing = CrmAppSetting(key=str(key))
                db.add(existing)
            existing.value = str(raw)
            existing.updated_at = _now_iso()
        if not dry_run:
            db.commit()
    except Exception:
        db.rollback()
        logger.exception("Falha importando crm_app_settings")
    finally:
        db.close()
    return count


def _import_account_users(*, dry_run: bool = False) -> int:
    users = _load_json_file("account_users.json", [])
    if not isinstance(users, list):
        return 0
    count = 0
    db = SessionLocal()
    try:
        for user in users:
            if not isinstance(user, dict):
                continue
            user_id = normalize_text(user.get("id"))
            if not user_id:
                continue
            count += 1
            if dry_run:
                continue
            existing = db.get(CrmAccountUser, user_id)
            if existing is None:
                existing = CrmAccountUser(id=user_id)
                db.add(existing)
            existing.name = normalize_text(user.get("name"))
            existing.email = normalize_text(user.get("email"))
            existing.username = normalize_text(user.get("username")).lower()
            existing.password_hash = normalize_text(user.get("password_hash"))
            existing.role = normalize_text(user.get("role")) or "Vendedor"
            existing.active = bool(user.get("active", True))
            existing.department_id = normalize_text(user.get("department_id"))
            existing.department_name = normalize_text(user.get("department_name"))
            existing.last_access = normalize_text(user.get("last_access"))
            existing.created_at = normalize_text(user.get("created_at")) or _now_iso()
            existing.updated_at = normalize_text(user.get("updated_at")) or _now_iso()
            existing.extras_json = "{}"
        if not dry_run:
            db.commit()
    except Exception:
        db.rollback()
        logger.exception("Falha importando crm_account_users")
    finally:
        db.close()
    return count


def _import_monthly_goals(*, dry_run: bool = False) -> int:
    goals = _load_json_file("monthly_goals.json", {})
    if not isinstance(goals, dict):
        return 0
    count = 0
    db = SessionLocal()
    try:
        for key, value in goals.items():
            try:
                period, seller = key.split("|", 1)
                year_text, month_text = period.split("-", 1)
                year, month = int(year_text), int(month_text)
            except Exception:
                continue
            if isinstance(value, dict):
                amount = float(value.get("amount") or 0)
                rate = float(value.get("commission_rate") or 8.0)
            else:
                try:
                    amount = float(value)
                except Exception:
                    continue
                rate = 8.0
            count += 1
            if dry_run:
                continue
            existing = (
                db.query(CrmMonthlyGoal)
                .filter(
                    CrmMonthlyGoal.reference_year == year,
                    CrmMonthlyGoal.reference_month == month,
                    CrmMonthlyGoal.seller == seller,
                )
                .first()
            )
            if existing is None:
                existing = CrmMonthlyGoal(
                    reference_year=year,
                    reference_month=month,
                    seller=seller,
                )
                db.add(existing)
            existing.amount = amount
            existing.commission_rate = rate
            existing.updated_at = _now_iso()
        if not dry_run:
            db.commit()
    except Exception:
        db.rollback()
        logger.exception("Falha importando crm_monthly_goals")
    finally:
        db.close()
    return count


def backfill_attendance_registration_ids() -> int:
    """Liga attendance_conversations.registration_id via sheet_row."""
    db = SessionLocal()
    updated = 0
    try:
        mapping = {
            int(r.sheet_row): int(r.id)
            for r in db.query(CrmRegistration).all()
            if r.sheet_row
        }
        if not mapping:
            return 0
        rows = (
            db.query(AttendanceConversation)
            .filter(AttendanceConversation.sheet_row.isnot(None))
            .all()
        )
        for conv in rows:
            sheet_row = int(conv.sheet_row or 0)
            reg_id = mapping.get(sheet_row)
            if not reg_id:
                continue
            if getattr(conv, "registration_id", None) == reg_id:
                continue
            conv.registration_id = reg_id
            updated += 1
        if updated:
            db.commit()
    except Exception:
        db.rollback()
        logger.exception("Falha no backfill attendance.registration_id")
    finally:
        db.close()
    return updated


def migrate_crm_to_postgres_if_needed(
    *,
    dry_run: bool = False,
    force: bool = False,
    sheet_force_refresh: bool = False,
) -> dict:
    """
    One-shot: Folha1 + JSONs locais → Postgres.
    Idempotente via AppMeta crm_postgres_migrated.
    """
    ensure_crm_schema()
    result = {
        "ran": False,
        "skipped": True,
        "reason": "",
        "registrations": {},
        "activities": 0,
        "proposals": 0,
        "settings": 0,
        "users": 0,
        "goals": 0,
        "attendance_linked": 0,
        "dry_run": dry_run,
    }

    db = SessionLocal()
    try:
        meta = db.get(AppMeta, MIGRATION_KEY)
        already = bool(meta and (meta.value or "").strip() in {"1", "true", "yes"})
        existing_regs = db.query(CrmRegistration).count()
    finally:
        db.close()

    if already and not force and not dry_run:
        result["reason"] = "already_migrated"
        # Still ensure attendance FK backfill for newer deploys
        result["attendance_linked"] = backfill_attendance_registration_ids()
        return result

    if existing_regs > 0 and not force and not dry_run:
        # Dados já no Postgres — só marca flag e backfill
        db = SessionLocal()
        try:
            db.merge(AppMeta(key=MIGRATION_KEY, value="1"))
            db.commit()
        finally:
            db.close()
        result["reason"] = "destination_already_has_data"
        result["attendance_linked"] = backfill_attendance_registration_ids()
        result["ran"] = True
        result["skipped"] = False
        return result

    try:
        result["registrations"] = _import_registrations_from_sheet(
            dry_run=dry_run,
            force_refresh=bool(sheet_force_refresh or force),
        )
    except Exception as error:
        logger.exception("Import crm_registrations abortou o cutover")
        result["ran"] = True
        result["skipped"] = False
        result["reason"] = "import_error"
        result["error"] = str(error)
        return result
    result["activities"] = _import_activities(dry_run=dry_run)
    result["proposals"] = _import_proposals(dry_run=dry_run)
    result["settings"] = _import_app_settings(dry_run=dry_run)
    result["users"] = _import_account_users(dry_run=dry_run)
    result["goals"] = _import_monthly_goals(dry_run=dry_run)

    regs_info = result.get("registrations") or {}
    imported_regs = int(regs_info.get("imported") or 0)
    source = normalize_text(regs_info.get("source"))
    has_aux = any(
        int(result.get(key) or 0) > 0
        for key in ("activities", "proposals", "settings", "users", "goals")
    )

    if not dry_run:
        # Nunca marcar migrado com Folha1 vazia (mesmo com force) — evita cutover vazio.
        # Aux JSON sozinho NÃO basta: Sem cadastros o CRM ficaria em branco.
        if imported_regs <= 0 and source == "empty":
            result["reason"] = "waiting_for_sheet_data"
            result["ran"] = True
            result["skipped"] = False
            result["has_aux"] = has_aux
            return result

        db = SessionLocal()
        try:
            total_regs = db.query(CrmRegistration).count()
        finally:
            db.close()
        if total_regs <= 0:
            result["reason"] = "import_produced_no_rows"
            result["ran"] = True
            result["skipped"] = False
            return result

        result["attendance_linked"] = backfill_attendance_registration_ids()
        db = SessionLocal()
        try:
            db.merge(AppMeta(key=MIGRATION_KEY, value="1"))
            db.commit()
        finally:
            db.close()
        try:
            from app.services.crm_registrations_storage import invalidate_registrations_cache

            invalidate_registrations_cache()
        except Exception:
            pass

    result["ran"] = True
    result["skipped"] = False
    result["reason"] = "migrated" if not dry_run else "dry_run"
    return result


_LAZY_MIGRATE_AT = 0.0
_LAZY_MIGRATE_TTL_SEC = 45.0


def reset_and_reimport_crm_from_folha1(*, dry_run: bool = False) -> dict:
    """
    Apaga cadastros e atividades no Postgres e recria os cadastros
    somente a partir da Folha1 (campos corretos da planilha base).

    Não apaga Atendimentos WhatsApp. Não reaproveita activities.json poluído.
    """
    ensure_crm_schema()
    result: dict = {
        "ok": False,
        "dry_run": dry_run,
        "deleted_registrations": 0,
        "deleted_activities": 0,
        "imported": 0,
        "source": "",
        "reason": "",
    }

    # 1) Confirma Folha1 antes de apagar
    preview = _import_registrations_from_sheet(dry_run=True, force_refresh=True)
    preview_count = int(preview.get("imported") or preview.get("sheet_rows") or 0)
    result["source"] = normalize_text(preview.get("source"))
    result["folha1_rows"] = preview_count
    if preview_count <= 0 or result["source"] == "empty":
        result["reason"] = "folha1_empty"
        result["preview"] = preview
        return result

    if dry_run:
        result["ok"] = True
        result["reason"] = "dry_run"
        result["would_import"] = preview_count
        return result

    # 2) Wipe Postgres (cadastros + atividades)
    db = SessionLocal()
    try:
        result["deleted_activities"] = int(db.query(CrmActivity).delete() or 0)
        result["deleted_registrations"] = int(db.query(CrmRegistration).delete() or 0)
        # Limpa flag para o cutover refletir o reimport limpo
        meta = db.get(AppMeta, MIGRATION_KEY)
        if meta is not None:
            db.delete(meta)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Falha ao apagar CRM no reset Folha1")
        result["reason"] = "wipe_failed"
        raise
    finally:
        db.close()

    # 3) Zera activities.json (evita reimportar cards errados de LEAD/WhatsApp)
    try:
        activities_path = get_storage_dir() / "activities.json"
        activities_path.parent.mkdir(parents=True, exist_ok=True)
        activities_path.write_text("{}", encoding="utf-8")
    except Exception:
        logger.exception("Falha ao limpar activities.json no reset")

    # 4) Reimporta só Folha1 → crm_registrations
    regs = _import_registrations_from_sheet(dry_run=False, force_refresh=True)
    result["registrations"] = regs
    result["imported"] = int(regs.get("imported") or 0)
    result["source"] = normalize_text(regs.get("source") or result["source"])

    if result["imported"] <= 0:
        result["reason"] = "import_produced_no_rows"
        return result

    # 5) Marca cutover + caches
    db = SessionLocal()
    try:
        db.merge(AppMeta(key=MIGRATION_KEY, value="1"))
        db.commit()
    finally:
        db.close()

    try:
        from app.services.crm_registrations_storage import invalidate_registrations_cache

        invalidate_registrations_cache()
    except Exception:
        pass
    try:
        from app.services.activities_storage import invalidate_activities_cache

        invalidate_activities_cache()
    except Exception:
        pass

    result["attendance_linked"] = backfill_attendance_registration_ids()
    result["ok"] = True
    result["reason"] = "reset_reimported"
    return result


def try_lazy_crm_postgres_cutover(*, bypass_throttle: bool = False) -> dict | None:
    """
    Tentativa throttled de cutover quando o flag ainda não está ligado.
    Preferir chamar DEPOIS da Folha1 aquecer o cache em memória.
    """
    global _LAZY_MIGRATE_AT
    import time

    try:
        from app.services.crm_registrations_storage import is_crm_postgres_ready

        if is_crm_postgres_ready():
            return None
    except Exception:
        return None

    now = time.monotonic()
    if not bypass_throttle and (now - _LAZY_MIGRATE_AT) < _LAZY_MIGRATE_TTL_SEC:
        return None
    _LAZY_MIGRATE_AT = now
    try:
        # Usa cache quente primeiro (sheet_force_refresh só libera API se cache vazio).
        return migrate_crm_to_postgres_if_needed(sheet_force_refresh=True)
    except Exception:
        logger.exception("Lazy CRM Postgres cutover falhou")
        return {"reason": "error", "ran": False}
