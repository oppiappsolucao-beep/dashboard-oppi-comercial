#!/usr/bin/env python3
"""Migração segura de valores legados do CRM — não executa alterações automaticamente."""
from __future__ import annotations

import argparse
import json
import shutil
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from config.legacy_option_maps import (
    LEGACY_ACTION_MAP,
    LEGACY_CHANNEL_MAP,
    LEGACY_OPPORTUNITY_STATUS_MAP,
    LEGACY_RESULT_MAP,
    LEGACY_STAGE_MAP,
    LEGACY_STATUS_MAP,
    MIGRATION_FIELD_LABELS,
)

ROOT = Path(__file__).resolve().parents[1]
STORAGE_FILES = [
    ROOT / "storage" / "activities.json",
    ROOT / "storage" / "lead_actions.json",
]
REPORT_DIR = ROOT / "storage" / "migration_reports"

FIELD_MAPS = {
    "stage": LEGACY_STAGE_MAP,
    "stage_override": LEGACY_STAGE_MAP,
    "process_action": LEGACY_ACTION_MAP,
    "title": LEGACY_ACTION_MAP,
    "next_action": LEGACY_ACTION_MAP,
    "next_action_description": LEGACY_ACTION_MAP,
    "result": LEGACY_RESULT_MAP,
    "channel": LEGACY_CHANNEL_MAP,
    "next_action_channel": LEGACY_CHANNEL_MAP,
    "status": LEGACY_STATUS_MAP,
    "opportunity_status": LEGACY_OPPORTUNITY_STATUS_MAP,
}


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return data if isinstance(data, dict) else {}


def _iter_records(data: dict):
    for tenant_id, bucket in data.items():
        if not isinstance(bucket, dict):
            continue
        activities = bucket.get("activities")
        if isinstance(activities, dict):
            for activity_id, record in activities.items():
                if isinstance(record, dict):
                    yield tenant_id, "activities", activity_id, record
            continue
        for sheet_row, record in bucket.items():
            if isinstance(record, dict):
                yield tenant_id, "lead_actions", str(sheet_row), record


def _collect_distinct_values() -> dict[str, Counter]:
    counters: dict[str, Counter] = defaultdict(Counter)
    for path in STORAGE_FILES:
        data = _load_json(path)
        for _, _, _, record in _iter_records(data):
            for field in FIELD_MAPS:
                value = record.get(field)
                if value is None:
                    continue
                text = str(value).strip()
                if text:
                    counters[field][text] += 1
    return counters


def _suggest(field: str, value: str) -> str:
    mapping = FIELD_MAPS.get(field, {})
    return mapping.get(value, "")


def build_report() -> list[dict]:
    counters = _collect_distinct_values()
    rows = []
    for field, counter in sorted(counters.items()):
        for value, count in counter.most_common():
            suggested = _suggest(field, value)
            rows.append({
                "field": field,
                "field_label": MIGRATION_FIELD_LABELS.get(field, field),
                "old_value": value,
                "count": count,
                "suggested_value": suggested or "(sem mapeamento — preservar)",
                "action": "migrate" if suggested else "preserve",
            })
    return rows


def _backup_files() -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = REPORT_DIR / f"backup_{timestamp}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    for path in STORAGE_FILES:
        if path.exists():
            shutil.copy2(path, backup_dir / path.name)
    return backup_dir


def apply_confirmed_migrations(rows: list[dict], dry_run: bool = True) -> dict:
    allowed = {
        (row["field"], row["old_value"])
        for row in rows
        if row.get("action") == "migrate" and row.get("suggested_value") and row["suggested_value"] != "(sem mapeamento — preservar)"
    }
    stats = {"updated": 0, "skipped": 0, "preserved": 0}
    log: list[dict] = []

    for path in STORAGE_FILES:
        data = _load_json(path)
        changed = False
        for tenant_id, source, record_id, record in _iter_records(data):
            for field, mapping in FIELD_MAPS.items():
                old_value = record.get(field)
                if old_value is None:
                    continue
                text = str(old_value).strip()
                if not text or (field, text) not in allowed:
                    continue
                new_value = mapping.get(text)
                if not new_value or new_value == text:
                    stats["skipped"] += 1
                    continue
                log.append({
                    "tenant_id": tenant_id,
                    "source": source,
                    "record_id": record_id,
                    "field": field,
                    "old_value": text,
                    "new_value": new_value,
                })
                if not dry_run:
                    record[field] = new_value
                    if source == "activities":
                        data.setdefault(tenant_id, {}).setdefault("activities", {})[record_id] = record
                    else:
                        data.setdefault(tenant_id, {})[record_id] = record
                    changed = True
                stats["updated"] += 1
        if changed and not dry_run:
            with path.open("w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2, default=str)
    return {"stats": stats, "log": log}


def main() -> None:
    parser = argparse.ArgumentParser(description="Relatório e migração segura de opções legadas do CRM")
    parser.add_argument("--apply", action="store_true", help="Aplica somente valores com mapeamento confirmado")
    parser.add_argument("--backup", action="store_true", help="Cria backup antes de aplicar")
    args = parser.parse_args()

    rows = build_report()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = REPORT_DIR / f"legacy_options_report_{timestamp}.json"
    with report_path.open("w", encoding="utf-8") as handle:
        json.dump(rows, handle, ensure_ascii=False, indent=2)

    print(f"Relatório gerado: {report_path}")
    print(f"Total de valores distintos: {len(rows)}")
    for row in rows:
        print(
            f"- [{row['field_label']}] '{row['old_value']}' ({row['count']}x) -> {row['suggested_value']}"
        )

    if args.apply:
        if args.backup:
            backup_dir = _backup_files()
            print(f"Backup criado em: {backup_dir}")
        result = apply_confirmed_migrations(rows, dry_run=False)
        print(f"Migração aplicada: {result['stats']}")
        log_path = REPORT_DIR / f"legacy_options_log_{timestamp}.json"
        with log_path.open("w", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2)
        print(f"Log de migração: {log_path}")
    else:
        print("Nenhuma alteração aplicada. Use --apply --backup para migrar valores mapeados.")


if __name__ == "__main__":
    main()
