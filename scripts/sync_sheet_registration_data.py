#!/usr/bin/env python3
"""Sincroniza cadastros existentes com as colunas corretas da planilha."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.sheet_registration_sync import sync_registration_rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Corrige endereços e serviços na planilha comercial.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Grava as correções na planilha. Sem essa flag, apenas simula.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limita a quantidade de linhas processadas (útil para teste).",
    )
    args = parser.parse_args()

    stats = sync_registration_rows(apply_changes=args.apply, limit=args.limit)
    mode = "APLICADO" if args.apply else "SIMULAÇÃO"
    print(f"[{mode}] Linhas analisadas: {stats['rows_seen']}")
    print(f"[{mode}] Linhas com correção: {stats['rows_updated']}")
    if not args.apply:
        print("Execute novamente com --apply para gravar na planilha.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
