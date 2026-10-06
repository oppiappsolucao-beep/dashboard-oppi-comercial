"""Grava o cadastro salvo na aba Raissa (clientes), a mesma lista de Empresas."""
from __future__ import annotations

import logging
import re
import unicodedata

log = logging.getLogger(__name__)


def normalize_text(value) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"nan", "none", "nat"}:
        return ""
    return re.sub(r"\s+", " ", text)


def cnpj_digits(value) -> str:
    digits = "".join(character for character in normalize_text(value) if character.isdigit())
    return digits if len(digits) == 14 else ""


def phone_key(value) -> str:
    digits = "".join(character for character in normalize_text(value) if character.isdigit())
    if len(digits) >= 11:
        return digits[-11:]
    if len(digits) >= 8:
        return digits
    return ""


def _remember_latest(index: dict[str, int], key: str, sheet_row: int) -> None:
    if not key:
        return
    if sheet_row >= index.get(key, 0):
        index[key] = sheet_row


def _remember_unique(index: dict[str, int], ambiguous: set[str], key: str, sheet_row: int) -> None:
    if not key or key in ambiguous:
        return
    previous = index.get(key)
    if previous and previous != sheet_row:
        ambiguous.add(key)
        index.pop(key, None)
        return
    index[key] = sheet_row


def _name_keys(item: dict) -> list[str]:
    keys = []
    for field in ("empresa", "nome_fantasia"):
        key = normalize_search_text(item.get(field))
        if key and key not in keys:
            keys.append(key)
    return keys


def _phone_keys(item: dict) -> list[str]:
    values = list(item.get("telefones") or [])
    if item.get("telefone"):
        values.append(item.get("telefone"))
    keys = []
    for value in values:
        key = phone_key(value)
        if key and key not in keys:
            keys.append(key)
    return keys


def match_cadastro_sheet_rows(empresas: list[dict], cadastros: list[dict]) -> list[dict]:
    """Liga cada empresa da aba Raissa ao cadastro, para o botão Ver aparecer."""
    by_cnpj: dict[str, int] = {}
    by_name: dict[str, int] = {}
    by_phone: dict[str, int] = {}
    ambiguous_phones: set[str] = set()
    named: list[tuple[int, str]] = []

    for item in cadastros or []:
        try:
            sheet_row = int(item.get("sheet_row") or 0)
        except (TypeError, ValueError):
            sheet_row = 0
        if sheet_row <= 0:
            continue
        _remember_latest(by_cnpj, cnpj_digits(item.get("cnpj")), sheet_row)
        for key in _name_keys(item):
            _remember_latest(by_name, key, sheet_row)
            named.append((sheet_row, key))
        for key in _phone_keys(item):
            _remember_unique(by_phone, ambiguous_phones, key, sheet_row)

    linked: list[dict] = []
    for item in empresas or []:
        sheet_row = by_cnpj.get(cnpj_digits(item.get("cnpj")), 0)
        if not sheet_row:
            for key in _name_keys(item):
                sheet_row = by_name.get(key, 0)
                if sheet_row:
                    break
        if not sheet_row:
            for key in _phone_keys(item):
                sheet_row = by_phone.get(key, 0)
                if sheet_row:
                    break
        if not sheet_row:
            sheet_row = _match_name_tokens(item.get("empresa"), named)
        copied = dict(item)
        copied["sheet_row"] = sheet_row
        linked.append(copied)
    return linked


def _match_name_tokens(empresa, named: list[tuple[int, str]]) -> int:
    tokens = [token for token in normalize_search_text(empresa).split() if len(token) >= 3]
    if len(tokens) < 2:
        return 0
    hits: dict[int, int] = {}
    for sheet_row, name in named:
        if all(token in name for token in tokens):
            hits[sheet_row] = hits.get(sheet_row, 0) + 1
    if not hits:
        return 0
    best = max(hits.values())
    return max(sheet_row for sheet_row, score in hits.items() if score == best)


def normalize_search_text(value) -> str:
    text = unicodedata.normalize("NFKD", normalize_text(value).lower())
    text = "".join(character for character in text if not unicodedata.combining(character))
    return re.sub(r"\s+", " ", text).strip()

_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "empresa": ("empresa", "nome da empresa", "nome empresas", "nome empresa", "razao social", "cliente"),
    "nome_fantasia": ("nome fantasia", "fantasia"),
    "cnpj": ("cnpj",),
    "data_abertura": ("data de abertura", "data abertura"),
    "data_fechamento": ("data de fechamento", "data fechamento"),
    "capital": ("capital social", "capital"),
    "endereco": ("endereco", "logradouro", "rua"),
    "endereco_numero": ("numero", "n", "no"),
    "endereco_complemento": ("complemento", "compl"),
    "cep": ("cep",),
    "bairro": ("bairro", "bairro/distrito", "distrito"),
    "municipio": ("municipio", "cidade"),
    "uf": ("uf", "estado"),
    "email": ("email", "e-mail", "email empresa"),
    "email_login_gestor": (
        "e-mail de login do gestor",
        "email de login do gestor",
        "e-mail login do gestor",
        "email login do gestor",
    ),
    "senha": ("senha", "senha de acesso"),
    "email_cobranca": (
        "email de cobranca",
        "e-mail de cobranca",
        "email cobranca",
        "e-mail cobranca",
        "email para cobranca",
        "e-mail para cobranca",
        "cobranca / email",
        "cobranca / e-mail",
        "cobranca/email",
        "cobranca/e-mail",
        "email / cobranca",
        "e-mail / cobranca",
    ),
    "site": ("site", "site empresa", "website"),
    "telefone": (
        "celular / whatsapp",
        "celular/whatsapp",
        "cobranca / whatsapp",
        "cobranca/whatsapp",
        "whatsapp",
        "celular whatsapp",
        "telefone b2b",
        "telefone (b2b)",
    ),
    "nome_contato": ("nome do contato", "nome contato", "contato"),
    "telefone_fixo": ("telefone fixo", "fixo"),
    "telefone_alternativo": ("telefone alternativo", "outro telefone", "telefone lemitt"),
    "socio_1": ("socio 1", "socio1"),
    "responsavel_legal": ("nome do responsavel", "responsavel legal", "responsavel"),
    "cpf_socio_1": ("cpf",),
    "email_socio_1": ("e-mail socio 1", "email socio 1", "e-mail do socio 1"),
    "nicho": ("nicho",),
    "vendedor": ("vendedor", "responsavel comercial"),
    "observacoes": ("observacoes", "observacao"),
    "instagram": ("instagram",),
    "linkedin": ("linkedin",),
    "filial": ("e filial", "filial", "is filial"),
    "status_cadastro": ("status", "status 1"),
    "matriz": ("empresa matriz", "nome da matriz", "matriz vinculada", "matriz"),
    "plano": ("plano", "plano / servico", "servico", "servicos fechados", "servico fechado"),
    "valor": ("valor", "valor do plano", "valor do servico", "valor da proposta"),
    "vencimento": ("data de vencimento", "primeiro vencimento", "vencimento"),
    "forma": ("forma de pagamento", "forma pagamento", "pagamento"),
    "ciclo": ("ciclo", "ciclo do plano"),
}

def _digits(value: str) -> str:
    return re.sub(r"\D", "", normalize_text(value))


def _header_map(headers: list[str]) -> dict[str, int]:
    """O primeiro alias da lista ganha, para não cair numa coluna mais genérica."""
    found: dict[str, int] = {}
    normalized = [normalize_search_text(header) for header in headers]
    for field, aliases in _FIELD_ALIASES.items():
        for alias in aliases:
            if alias in normalized:
                found[field] = normalized.index(alias)
                break
    return found


def _header_indexes(headers: list[str], aliases: tuple[str, ...]) -> list[int]:
    wanted = set(aliases)
    return [
        index
        for index, header in enumerate(headers)
        if normalize_search_text(header) in wanted
    ]


def _is_billing_email_header(header: str) -> bool:
    """Coluna de e-mail de cobrança, mesmo com barra ou a palavra 'para'."""
    plain = normalize_search_text(header)
    folded = plain.replace("-", "").replace("/", " ")
    folded = re.sub(r"\s+", " ", folded)
    has_mail = "email" in folded or "e mail" in plain
    has_bill = "cobranca" in folded
    has_phone = any(word in folded for word in ("whatsapp", "telefone", "celular", "fone"))
    return has_mail and has_bill and not has_phone


def apply_raissa_values(row: list[str], headers: list[str], payload: dict, *, only_filled: bool) -> list[str]:
    """Preenche a linha pelos cabeçalhos que já existem. Não apaga célula com valor vazio."""
    width = max(len(headers), len(row))
    values = list(row) + [""] * (width - len(row))
    mapping = _header_map(headers)
    multi_fields = {"telefone", "email_cobranca"}
    for field, index in mapping.items():
        if field in multi_fields:
            continue
        incoming = normalize_text(payload.get(field))
        if only_filled and not incoming:
            continue
        if index < len(values):
            values[index] = incoming
    for field in multi_fields:
        incoming = normalize_text(payload.get(field))
        if only_filled and not incoming:
            continue
        indexes = _header_indexes(headers, _FIELD_ALIASES[field])
        if field == "email_cobranca":
            indexes = [
                index
                for index, header in enumerate(headers)
                if _is_billing_email_header(header)
            ]
        for index in indexes:
            if index < len(values):
                values[index] = incoming
    return values[: max(len(headers), 1)]


def ensure_raissa_columns(headers: list[str]) -> list[str]:
    """Não cria coluna. O comercial já tem os nomes na planilha."""
    return list(headers)


_CYCLE_LABELS = {"mensal": "Mensal", "anual": "Anual", "avulso": "Avulso"}
_FORMA_LABELS = {
    "cartao_recorrente": "Cartão recorrente",
    "boleto_recorrente": "Boleto recorrente",
    "cartao_avulso": "Cartão de crédito (Avulso)",
    "pix_boleto": "Pix/Boleto (Avulso)",
    "pix": "PIX Avulso",
}


def _sheet_date(value) -> str:
    raw = normalize_text(value)[:10]
    if len(raw) == 10 and raw[4] == "-" and raw[7] == "-":
        year, month, day = raw.split("-")
        return f"{day}/{month}/{year}"
    return raw


def _billing_labels(billing: dict | None) -> dict[str, str]:
    data = billing or {}

    def cycle_label(key: str) -> str:
        return _CYCLE_LABELS.get(normalize_text(key).lower(), normalize_text(key))

    def forma_label(key: str) -> str:
        return _FORMA_LABELS.get(normalize_text(key).lower(), normalize_text(key))

    return {
        "plano": normalize_text(data.get("servico") or data.get("plano")),
        "valor": normalize_text(data.get("valor")),
        "vencimento": _sheet_date(data.get("vencimento")),
        "forma": forma_label(data.get("forma")) if normalize_text(data.get("forma")) else "",
        "ciclo": cycle_label(data.get("ciclo")) if normalize_text(data.get("ciclo")) else "",
    }


def payload_for_raissa(form: dict, billing: dict | None = None) -> dict:
    finance = _billing_labels(billing)
    if not finance["plano"]:
        finance["plano"] = normalize_text(form.get("servico") or form.get("billing_servico"))
    if not finance["valor"]:
        finance["valor"] = normalize_text(form.get("valor_proposta") or form.get("billing_valor"))
    if not finance["vencimento"]:
        finance["vencimento"] = _sheet_date(form.get("billing_vencimento"))
    if not finance["forma"] and normalize_text(form.get("billing_forma")):
        finance["forma"] = _FORMA_LABELS.get(normalize_text(form.get("billing_forma")).lower(), normalize_text(form.get("billing_forma")))
    if not finance["ciclo"] and normalize_text(form.get("billing_ciclo")):
        finance["ciclo"] = _CYCLE_LABELS.get(normalize_text(form.get("billing_ciclo")).lower(), normalize_text(form.get("billing_ciclo")))
    filial = normalize_text(form.get("is_filial")).lower() in {"1", "on", "true", "yes", "sim"}
    return {
        "empresa": normalize_text(form.get("empresa")),
        "nome_fantasia": normalize_text(form.get("nome_fantasia")),
        "cnpj": normalize_text(form.get("cnpj")),
        "data_abertura": normalize_text(form.get("data_abertura")),
        "data_fechamento": normalize_text(form.get("data_fechamento")),
        "responsavel_legal": normalize_text(form.get("responsavel_legal") or form.get("nome_responsavel") or form.get("socio_1")),
        "capital": normalize_text(form.get("capital")),
        "endereco": normalize_text(form.get("endereco")),
        "endereco_numero": normalize_text(form.get("endereco_numero")),
        "endereco_complemento": normalize_text(form.get("endereco_complemento")),
        "cep": normalize_text(form.get("cep")),
        "bairro": normalize_text(form.get("bairro")),
        "municipio": normalize_text(form.get("municipio")),
        "uf": normalize_text(form.get("uf")).upper()[:2],
        "email": normalize_text(form.get("email_empresa") or form.get("email")),
        "email_login_gestor": normalize_text(form.get("email_login_gestor")),
        "email_cobranca": normalize_text(form.get("email_cobranca")),
        "senha": normalize_text(form.get("senha_acesso") or form.get("senha")),
        "site": normalize_text(form.get("site")),
        "telefone": normalize_text(form.get("telefone_b2b")),
        "nome_contato": normalize_text(form.get("nome_contato") or form.get("socio_1")),
        "telefone_fixo": normalize_text(form.get("telefone_fixo")),
        "telefone_alternativo": normalize_text(form.get("telefone_alternativo")),
        "instagram": normalize_text(form.get("instagram")),
        "linkedin": normalize_text(form.get("linkedin")),
        "socio_1": normalize_text(form.get("socio_1")),
        "cpf_socio_1": normalize_text(form.get("cpf_socio_1")),
        "email_socio_1": normalize_text(form.get("email_socio_1") or form.get("email_login_gestor")),
        "nicho": normalize_text(form.get("nicho")),
        "vendedor": normalize_text(form.get("vendedor")),
        "observacoes": normalize_text(form.get("observacoes")),
        "filial": "Filial" if filial else "Matriz",
        "matriz": normalize_text(form.get("empresa_matriz_search") or form.get("empresa_matriz_nome")),
        **finance,
    }


def _header_at(values: list[list[str]]) -> int:
    if len(values) > 1:
        filled_first = sum(1 for cell in values[0] if normalize_text(cell))
        filled_second = sum(1 for cell in values[1] if normalize_text(cell))
        if filled_first <= 1 and filled_second >= 3:
            return 1
    return 0


def _find_row(values: list[list[str]], header_at: int, headers: list[str], payload: dict) -> int | None:
    mapping = _header_map(headers)
    cnpj_index = mapping.get("cnpj")
    name_index = mapping.get("empresa")
    target_cnpj = _digits(payload.get("cnpj", ""))
    target_name = normalize_search_text(payload.get("empresa"))
    for offset, raw in enumerate(values[header_at + 1 :], start=header_at + 2):
        row = list(raw)
        if target_cnpj and cnpj_index is not None and cnpj_index < len(row):
            if _digits(row[cnpj_index]) == target_cnpj:
                return offset
        if target_name and name_index is not None and name_index < len(row):
            if normalize_search_text(row[name_index]) == target_name:
                return offset
    return None


def save_registration_on_raissa(form: dict, billing: dict | None = None) -> dict:
    """Cria ou atualiza o cliente na aba Raissa. Não levanta: devolve ok/aviso."""
    payload = payload_for_raissa(form, billing)
    if not payload.get("empresa"):
        return {"ok": False, "aviso": "Sem nome de empresa para gravar na aba Raissa."}
    try:
        from app.config import settings
        from app.services.campaign_leads import _find_raissa_company_worksheet
        from app.services.legacy_core import get_gsheet_client
        from app.services.sheet_read_cache import invalidate_worksheet_cache

        if not settings.sheets_configured:
            return {"ok": False, "aviso": "Planilha não configurada."}
        client = get_gsheet_client()
        spreadsheet = client.open_by_key(settings.sheet_id)
        worksheet = _find_raissa_company_worksheet(spreadsheet)
        if worksheet is None:
            return {"ok": False, "aviso": "Aba Raissa não encontrada na planilha."}
        values = worksheet.get_all_values() or [[]]
        header_at = _header_at(values)
        headers = [normalize_text(cell) for cell in (values[header_at] if values else [])]
        headers = ensure_raissa_columns(headers)
        if headers != [normalize_text(cell) for cell in (values[header_at] if values else [])]:
            worksheet.update(
                f"A{header_at + 1}",
                [headers],
                value_input_option="USER_ENTERED",
            )
        row_number = _find_row(values, header_at, headers, payload)
        if not row_number:
            payload["status_cadastro"] = "1 Boleto Aguardando"
        existing = []
        if row_number and row_number - 1 < len(values):
            existing = values[row_number - 1]
        row_values = apply_raissa_values(existing, headers, payload, only_filled=bool(row_number))
        if not row_number:
            row_number = max(len(values), header_at + 1) + 1
            if not any(normalize_text(cell) for cell in (values[-1] if values else [])):
                row_number = max(len(values), header_at + 1)
        if row_number > int(worksheet.row_count or 0):
            worksheet.add_rows(row_number - int(worksheet.row_count) + 5)
        worksheet.update(
            f"A{row_number}",
            [row_values],
            value_input_option="USER_ENTERED",
        )
        invalidate_worksheet_cache(worksheet.title)
        return {"ok": True, "aba": worksheet.title, "linha": row_number, "aviso": ""}
    except Exception as exc:
        log.warning("Falha ao gravar cadastro na aba Raissa: %s", exc)
        return {"ok": False, "aviso": "Não consegui gravar na aba Raissa agora. O cadastro no sistema foi salvo."}
