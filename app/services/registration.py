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
    "empresa", "data_abertura", "capital", "cnpj", "endereco", "endereco_numero", "endereco_complemento",
    "cep", "bairro", "municipio", "uf", "email_empresa", "site",
    "telefone_b2b", "telefone_fixo", "telefone_alternativo",
    "socio_1", "cpf_socio_1", "email_socio_1", "telefone_socio_1",
    "socio_2", "telefone_socio_2", "cpf_socio_2",
    "socio_3", "telefone_socio_3", "cpf_socio_3",
    "instagram", "linkedin", "vendedor", "status", "data_chamado", "observacoes",
    "servico", "valor_proposta", "colaboradores",
]


def infer_partners_count(values: dict) -> int:
    if any(normalize_text(values.get(key)) for key in ("socio_3", "telefone_socio_3", "cpf_socio_3")):
        return 3
    if any(normalize_text(values.get(key)) for key in ("socio_2", "telefone_socio_2", "cpf_socio_2")):
        return 2
    if any(normalize_text(values.get(key)) for key in ("socio_1", "telefone_socio_1", "cpf_socio_1", "email_socio_1")):
        return 1
    return 0


def validate_registration_form(form: dict) -> str | None:
    empresa = normalize_text(form.get("empresa"))
    cnpj = normalize_text(form.get("cnpj"))
    telefone_b2b = normalize_text(form.get("telefone_b2b"))
    telefone_fixo = normalize_text(form.get("telefone_fixo"))
    telefone_alternativo = normalize_text(form.get("telefone_alternativo"))

    if not empresa:
        return "Preencha o nome da empresa para concluir o cadastro."
    if cnpj and not normalize_cnpj_for_duplicate(cnpj):
        return "Digite um CNPJ válido com 14 números."

    for label, phone in [
        ("Celular WhatsApp", telefone_b2b),
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


def save_new_company(form: dict) -> int:
    error = validate_registration_form(form)
    if error:
        raise ValueError(error)
    return append_company_to_sheet(build_registration_payload(form))


def save_company_edit(sheet_row: int, form: dict) -> None:
    error = validate_registration_form(form)
    if error:
        raise ValueError(error)
    update_company_in_sheet(sheet_row, build_registration_payload(form))


def get_seller_options(df) -> list[str]:
    sellers = sorted({
        normalize_text(v) for v in df["_vendedor"].tolist()
        if normalize_text(v) and normalize_text(v) != "Sem vendedor"
    })
    return sellers or ["Sem vendedor"]
