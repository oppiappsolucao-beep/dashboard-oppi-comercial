"""Validação e payloads de cadastro."""
from datetime import date

import pandas as pd

from app.services.legacy_core import (
    DuplicateRegistrationError,
    STATUS_OPTIONS,
    append_company_to_sheet,
    normalize_cnpj_for_duplicate,
    normalize_phone_for_duplicate,
    normalize_text,
    update_company_in_sheet,
)


REGISTRATION_FIELDS = [
    "empresa", "data_abertura", "capital", "cnpj", "endereco", "email_empresa", "site",
    "telefone_b2b", "telefone_fixo", "telefone_alternativo",
    "socio_1", "cpf_socio_1", "email_socio_1", "telefone_socio_1",
    "socio_2", "telefone_socio_2", "cpf_socio_2",
    "socio_3", "telefone_socio_3", "cpf_socio_3",
    "instagram", "linkedin", "vendedor", "status", "data_chamado", "observacoes",
]


def validate_registration_form(form: dict, require_all_phones: bool = True) -> str | None:
    empresa = normalize_text(form.get("empresa"))
    cnpj = normalize_text(form.get("cnpj"))
    telefone_b2b = normalize_text(form.get("telefone_b2b"))
    telefone_fixo = normalize_text(form.get("telefone_fixo"))
    telefone_alternativo = normalize_text(form.get("telefone_alternativo"))

    if not empresa:
        return "Preencha o nome da empresa para concluir o cadastro."
    if not cnpj:
        return "Preencha o CNPJ para concluir o cadastro."
    if not normalize_cnpj_for_duplicate(cnpj):
        return "Digite um CNPJ válido com 14 números."

    if require_all_phones:
        if not telefone_b2b:
            return "Preencha o telefone B2B para concluir o cadastro."
        if not telefone_fixo:
            return "Preencha o telefone fixo para concluir o cadastro."
        if not telefone_alternativo:
            return "Preencha o telefone alternativo para concluir o cadastro."

    for label, phone in [
        ("Telefone B2B", telefone_b2b),
        ("Telefone fixo", telefone_fixo),
        ("Telefone alternativo", telefone_alternativo),
    ]:
        if phone and not normalize_phone_for_duplicate(phone):
            return f"Digite um número válido no campo {label}."

    return None


def build_registration_payload(form: dict) -> dict:
    now_text = pd.Timestamp.now(tz="America/Sao_Paulo").strftime("%d/%m/%Y %H:%M")
    data_chamado = form.get("data_chamado") or date.today().strftime("%d/%m/%Y")

    if hasattr(data_chamado, "strftime"):
        data_chamado = data_chamado.strftime("%d/%m/%Y")

    payload = {field: normalize_text(form.get(field, "")) for field in REGISTRATION_FIELDS}
    payload["data_chamado"] = normalize_text(data_chamado)
    payload["ultima_atualizacao"] = now_text
    return payload


def save_new_company(form: dict) -> None:
    error = validate_registration_form(form)
    if error:
        raise ValueError(error)
    append_company_to_sheet(build_registration_payload(form))


def save_company_edit(sheet_row: int, form: dict) -> None:
    error = validate_registration_form(form, require_all_phones=False)
    if error:
        raise ValueError(error)
    update_company_in_sheet(sheet_row, build_registration_payload(form))


def get_seller_options(df) -> list[str]:
    sellers = sorted({
        normalize_text(v) for v in df["_vendedor"].tolist()
        if normalize_text(v) and normalize_text(v) != "Sem vendedor"
    })
    return sellers or ["Sem vendedor"]
