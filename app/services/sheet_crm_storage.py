"""Abas auxiliares da planilha para persistir configurações do CRM."""
from __future__ import annotations

from gspread.exceptions import WorksheetNotFound

from app.config import settings
from app.services.legacy_core import get_gsheet_client, normalize_text

CRM_STORAGE_TABS = {
    "Usuarios": ["Id", "Nome", "Email", "Usuario", "SenhaHash", "Perfil", "Ativo", "UltimoAcesso", "CriadoEm", "AtualizadoEm"],
    "Metas": ["Ano", "Mes", "Vendedor", "Meta", "Comissao"],
    "Configuracoes": ["Chave", "Valor"],
    "Atividades": [
        "Id",
        "TenantId",
        "SheetRow",
        "Empresa",
        "Status",
        "Etapa",
        "Acao",
        "Responsavel",
        "AgendadoEm",
        "Excluido",
        "Dados",
    ],
    "LeadAcoes": ["TenantId", "SheetRow", "AtualizadoEm", "Dados"],
}


def _open_spreadsheet():
    if not settings.sheets_configured:
        return None
    client = get_gsheet_client()
    return client.open_by_key(settings.sheet_id)


def ensure_crm_storage_tabs() -> dict[str, bool]:
    """Garante que as abas auxiliares do CRM existam na planilha."""
    result = {name: False for name in CRM_STORAGE_TABS}
    spreadsheet = _open_spreadsheet()
    if spreadsheet is None:
        return result

    for tab_name, headers in CRM_STORAGE_TABS.items():
        try:
            worksheet = spreadsheet.worksheet(tab_name)
            existing = worksheet.row_values(1)
            if not existing or header_indexes(existing, headers) is None:
                worksheet.update([headers], "A1", value_input_option="USER_ENTERED")
            result[tab_name] = True
        except WorksheetNotFound:
            try:
                worksheet = spreadsheet.add_worksheet(tab_name, rows=300, cols=max(len(headers), 4))
                worksheet.update([headers], "A1", value_input_option="USER_ENTERED")
                result[tab_name] = True
            except Exception:
                result[tab_name] = False
        except Exception:
            result[tab_name] = False
    return result


def get_worksheet(tab_name: str):
    spreadsheet = _open_spreadsheet()
    if spreadsheet is None:
        return None
    try:
        return spreadsheet.worksheet(tab_name)
    except WorksheetNotFound:
        ensure_crm_storage_tabs()
        try:
            return spreadsheet.worksheet(tab_name)
        except Exception:
            return None
    except Exception:
        return None


def header_indexes(headers_row: list[str], expected: list[str]) -> dict[str, int] | None:
    normalized = [normalize_text(cell).lower() for cell in headers_row]
    indexes: dict[str, int] = {}
    for header in expected:
        key = normalize_text(header).lower()
        try:
            indexes[header] = normalized.index(key)
        except ValueError:
            return None
    return indexes
