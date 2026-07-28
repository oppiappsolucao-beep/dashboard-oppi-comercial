#!/usr/bin/env python3
"""Importa clientes do Oppi Ponto no CRM como EMPRESA (não lead).

Uso:
  1) No Oppi Dev → Empresas → "Exportar p/ CRM" (baixa JSON)
  2) Dry-run (padrão, não grava):
       python scripts/migrate_ponto_empresas.py caminho/do/export.json
  3) Aplicar:
       python scripts/migrate_ponto_empresas.py caminho/do/export.json --apply

Requer planilha Google configurada (.env do CRM).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrar clientes Oppi Ponto → CRM como empresa")
    parser.add_argument("json_path", type=Path, help="Arquivo JSON exportado do Oppi Ponto")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Grava na planilha/CRM. Sem esta flag, só simula (dry-run).",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Caminho opcional para salvar o relatório JSON",
    )
    args = parser.parse_args()

    if not args.json_path.exists():
        print(f"Arquivo não encontrado: {args.json_path}", file=sys.stderr)
        return 1

    payload = json.loads(args.json_path.read_text(encoding="utf-8"))

    from app.services.ponto_migration import migrate_companies

    summary = migrate_companies(payload, apply=bool(args.apply))

    report_path = args.report
    if report_path is None:
        stamp = "apply" if args.apply else "dry-run"
        report_path = ROOT / "storage" / "migration_reports" / f"ponto-empresas-{stamp}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] total={summary['total']} create={summary['create']} update={summary['update']} skip_cnpj={summary['skip_missing_cnpj']}")
    if args.apply:
        print(f"ok={summary['ok']} failed={summary['failed']}")
    print(f"Relatório: {report_path}")

    if summary["skip_missing_cnpj"]:
        print("Atenção: alguns registros sem CNPJ válido foram ignorados.")
    if args.apply and summary["failed"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
