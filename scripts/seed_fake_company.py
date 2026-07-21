"""Cadastra empresa FAKE de teste na planilha com todos os campos preenchidos."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.seed_fake_company import seed_fake_test_company


def main() -> int:
    try:
        result = seed_fake_test_company(user="seed_script")
    except RuntimeError as error:
        print(f"ERRO: {error}")
        return 1
    except Exception as error:
        print(f"ERRO: {error}")
        return 1

    print(result["message"])
    print(f"Linha: {result['sheet_row']} | {result['empresa']}")
    print(f"Edição: {result['edit_url']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
