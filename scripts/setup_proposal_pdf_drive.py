"""Limpeza da cota do Drive da service account e diagnóstico do motor de PDF."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.app_settings import get_proposal_template_doc_id  # noqa: E402
from app.services.proposal_pdf import (  # noqa: E402
    check_pdf_engine_status,
    cleanup_service_account_proposal_files,
)
from app.services.legacy_core import normalize_text  # noqa: E402


def main() -> int:
    print("Motor de PDF:", json.dumps(check_pdf_engine_status(), ensure_ascii=False))
    template_id = get_proposal_template_doc_id()
    print(f"Modelo configurado: {template_id or 'não definido'}")
    deleted = cleanup_service_account_proposal_files(keep_template_id=template_id)
    print(f"Arquivos temporários removidos da service account: {deleted}")
    print(
        normalize_text(
            "Próximo passo: rebuild no Easypanel e teste /health/pdf-engine em produção."
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
