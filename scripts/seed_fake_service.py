"""Cadastra serviço FAKE de teste na aba Configuracoes da planilha."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.seed_fake_service import seed_fake_test_service


def main() -> int:
    try:
        result = seed_fake_test_service()
    except RuntimeError as error:
        print(f"ERRO: {error}")
        return 1
    except Exception as error:
        print(f"ERRO: {error}")
        return 1

    print(result["message"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
