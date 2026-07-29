"""CLI: migração CRM → Postgres (dry-run ou force).

Uso:
  python -m scripts.migrate_crm_to_postgres --dry-run
  python -m scripts.migrate_crm_to_postgres
  python -m scripts.migrate_crm_to_postgres --force
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="Migra CRM (Folha1/JSON) → Postgres SoT")
    parser.add_argument("--dry-run", action="store_true", help="Só conta, não grava")
    parser.add_argument("--force", action="store_true", help="Reimporta mesmo se já migrado")
    args = parser.parse_args()

    from app.services.crm_db_migrate import migrate_crm_to_postgres_if_needed

    result = migrate_crm_to_postgres_if_needed(dry_run=args.dry_run, force=args.force)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
