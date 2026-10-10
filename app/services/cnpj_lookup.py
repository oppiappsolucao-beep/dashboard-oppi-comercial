"""Consulta CNPJ e monta o preenchimento do cadastro (nicho pelo CNAE e 1 responsável)."""
from __future__ import annotations

import json
import re
import secrets
import string
import unicodedata
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"nan", "none", "nat"}:
        return ""
    return re.sub(r"\s+", " ", text)


def _normalize_search_text(value: Any) -> str:
    text = unicodedata.normalize("NFKD", _normalize_text(value).lower())
    text = "".join(character for character in text if not unicodedata.combining(character))
    return re.sub(r"\s+", " ", text).strip()


def _cnpj_digits(value: Any) -> str:
    digits = re.sub(r"\D", "", _normalize_text(value))
    return digits if len(digits) == 14 else ""

_BRASILAPI_URL = "https://brasilapi.com.br/api/cnpj/v1/{cnpj}"
_RECEITAWS_URL = "https://www.receitaws.com.br/v1/cnpj/{cnpj}"
_TIMEOUT = 12

# Divisão CNAE (2 primeiros dígitos) → nicho do cadastro.
_DIVISION_NICHES = {
    **{f"{code:02d}": "Agronegócio" for code in range(1, 4)},
    **{f"{code:02d}": "Indústria" for code in range(5, 36)},
    "36": "Serviços",
    "37": "Serviços",
    "38": "Serviços",
    "39": "Serviços",
    "41": "Construção e Imóveis",
    "42": "Construção e Imóveis",
    "43": "Construção e Imóveis",
    "45": "Automotivo",
    "46": "Comércio e Varejo",
    "47": "Comércio e Varejo",
    "49": "Transporte e Logística",
    "50": "Transporte e Logística",
    "51": "Transporte e Logística",
    "52": "Transporte e Logística",
    "53": "Transporte e Logística",
    "55": "Turismo e Eventos",
    "56": "Alimentação",
    "58": "Tecnologia",
    "59": "Marketing e Comunicação",
    "60": "Marketing e Comunicação",
    "61": "Telecomunicações",
    "62": "Tecnologia",
    "63": "Tecnologia",
    "64": "Financeiro e Contábil",
    "65": "Financeiro e Contábil",
    "66": "Financeiro e Contábil",
    "68": "Construção e Imóveis",
    "69": "Jurídico",
    "70": "Serviços",
    "71": "Construção e Imóveis",
    "72": "Tecnologia",
    "73": "Marketing e Comunicação",
    "74": "Serviços",
    "75": "Pet",
    "77": "Serviços",
    "78": "Serviços",
    "79": "Turismo e Eventos",
    "80": "Serviços",
    "81": "Serviços",
    "82": "Serviços",
    "85": "Educação",
    "86": "Saúde e Bem-estar",
    "87": "Saúde e Bem-estar",
    "88": "Saúde e Bem-estar",
    "90": "Turismo e Eventos",
    "91": "Turismo e Eventos",
    "92": "Turismo e Eventos",
    "93": "Saúde e Bem-estar",
    "94": "Serviços",
    "95": "Serviços",
    "96": "Serviços",
}

# Descrição do CNAE ganha da divisão quando o texto é mais específico.
_KEYWORD_NICHES = (
    ("Automotivo", ("automot", "veiculo", "automotor", "autopeca", "oficina mecanica", "pneumatic")),
    ("Pet", ("pet shop", "veterinar", "animais de estimacao")),
    ("Beleza e Estética", ("estetic", "cabeleireir", "barbear", "salao de beleza", "manicure", "depilacao")),
    ("Saúde e Bem-estar", ("odontolog", "hospital", "clinica", "medico", "farmac", "saude", "fisioterap", "academia")),
    ("Alimentação", ("restaurante", "lanchonete", "padaria", "aliment", "pizzaria", "sorveteria", "cafeteria")),
    ("Educação", ("educacao", "ensino", "escola", "creche")),
    ("Tecnologia", ("software", "programa de computador", "tecnologia da informacao", "desenvolvimento de programas")),
    ("Telecomunicações", ("telecomunic", "telefonia")),
    ("Jurídico", ("advocac", "juridic", "cartorio", "notaria")),
    ("Financeiro e Contábil", ("contabil", "contab", "auditoria contabil", "atividade financeira")),
    ("Marketing e Comunicação", ("publicidade", "propaganda", "marketing")),
    ("Transporte e Logística", ("transporte de carga", "logistica", "armazenagem")),
    ("Construção e Imóveis", ("construcao de", "incorporacao", "imobiliari", "engenharia civil")),
    ("Agronegócio", ("agricultura", "pecuaria", "cultivo de", "agropec")),
    ("Turismo e Eventos", ("hotelaria", "turismo", "agencia de viagem", "organizacao de eventos")),
    ("Indústria", ("fabricacao de", "industria de")),
)

_ADMIN_QUALIFICATIONS = {5, 10, 16, 49}


class CnpjLookupError(ValueError):
    pass


def niche_from_cnae(code: str, description: str = "") -> str:
    """Escolhe o nicho do cadastro a partir do CNAE principal."""
    digits = re.sub(r"\D", "", str(code or ""))
    text = _normalize_search_text(description)
    for niche, keywords in _KEYWORD_NICHES:
        if any(keyword in text for keyword in keywords):
            return niche
    if len(digits) >= 2:
        return _DIVISION_NICHES.get(digits[:2], "Outros")
    return ""


def generate_access_password(length: int = 10) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(max(8, length)))


def _format_date(value: Any) -> str:
    text = _normalize_text(value)[:10]
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        year, month, day = text.split("-")
        return f"{day}/{month}/{year}"
    if len(text) == 10 and text[2] == "/" and text[5] == "/":
        return text
    return ""


def _format_capital(value: Any) -> str:
    if value in (None, ""):
        return ""
    text = _normalize_text(value).replace("R$", "").strip()
    if not text:
        return ""
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        number = float(text)
    except ValueError:
        return _normalize_text(value)
    formatted = f"{number:,.2f}"
    return formatted.replace(",", "X").replace(".", ",").replace("X", ".")


def _format_cep(value: Any) -> str:
    digits = re.sub(r"\D", "", _normalize_text(value))
    if len(digits) != 8:
        return _normalize_text(value)
    return f"{digits[:5]}-{digits[5:]}"


def _format_phone(value: Any) -> str:
    return re.sub(r"\D", "", _normalize_text(value))


def _pick_email(*candidates: Any) -> str:
    for value in candidates:
        text = _normalize_text(value)
        if text and "@" in text:
            return text
    return ""


def _cnae_digits(value: Any) -> str:
    return re.sub(r"\D", "", _normalize_text(value))


def _first_partner(qsa: list[dict] | None) -> dict:
    members = [item for item in (qsa or []) if isinstance(item, dict)]
    if not members:
        return {}

    def rank(item: dict) -> tuple[int, str]:
        code = item.get("codigo_qualificacao_socio")
        try:
            code_int = int(code)
        except (TypeError, ValueError):
            code_int = 0
        qual = _normalize_search_text(item.get("qual") or item.get("qualificacao") or "")
        preferred = code_int in _ADMIN_QUALIFICATIONS or "administrador" in qual or "diretor" in qual
        name = _normalize_text(item.get("nome_socio") or item.get("nome"))
        return (0 if preferred else 1, name)

    chosen = sorted(members, key=rank)[0]
    name = _normalize_text(chosen.get("nome_socio") or chosen.get("nome"))
    document = re.sub(r"\D", "", _normalize_text(chosen.get("cnpj_cpf_do_socio") or chosen.get("cpf")))
    cpf = document if len(document) == 11 else ""
    return {"nome": name, "cpf": cpf}


def _single_owner_name(raw: dict, empresa: str, partner_name: str) -> str:
    """Empresário individual não vem no QSA: o responsável é a própria razão social."""
    if partner_name:
        return partner_name
    razao = _normalize_text(raw.get("razao_social") or raw.get("nome"))
    if not razao:
        return ""
    natureza = _normalize_search_text(raw.get("natureza_juridica"))
    individual = bool(raw.get("opcao_pelo_mei")) or any(
        token in natureza for token in ("individual", "empresario", "mei")
    )
    corporate = any(
        token in _normalize_search_text(razao)
        for token in ("ltda", "eireli", "s/a", "s.a.", "sociedade")
    )
    if individual or (empresa and razao.casefold() != empresa.casefold() and not corporate):
        return razao
    return ""


def registration_from_cnpj_payload(raw: dict, *, password: str | None = None) -> dict[str, str]:
    """Normaliza a resposta da Receita/BrasilAPI para os campos do formulário."""
    cnae_code = _cnae_digits(raw.get("cnae_fiscal") or raw.get("cnae"))
    cnae_description = _normalize_text(raw.get("cnae_fiscal_descricao") or raw.get("cnae_descricao"))
    activities = raw.get("atividade_principal") or []
    if not cnae_code and isinstance(activities, list) and activities:
        first = activities[0] if isinstance(activities[0], dict) else {}
        cnae_code = _cnae_digits(first.get("code"))
        cnae_description = cnae_description or _normalize_text(first.get("text"))

    email = _pick_email(raw.get("email"))
    partner = _first_partner(raw.get("qsa"))
    niche = niche_from_cnae(cnae_code, cnae_description)
    phones = [
        phone
        for phone in (
            _format_phone(raw.get("ddd_telefone_1")),
            _format_phone(raw.get("ddd_telefone_2")),
            _format_phone(raw.get("telefone")),
        )
        if phone
    ]
    fantasia = _normalize_text(raw.get("nome_fantasia") or raw.get("fantasia"))
    razao = _normalize_text(raw.get("razao_social") or raw.get("nome"))
    empresa = fantasia or razao
    owner = _single_owner_name(raw, empresa, partner.get("nome", ""))
    generated = password if password is not None else generate_access_password()

    payload = {
        "empresa": empresa,
        "data_abertura": _format_date(raw.get("data_inicio_atividade") or raw.get("abertura")),
        "capital": _format_capital(raw.get("capital_social")),
        "email": email,
        "telefone": phones[0] if phones else "",
        "telefone_2": phones[1] if len(phones) > 1 else "",
        "cep": _format_cep(raw.get("cep")),
        "endereco": _normalize_text(raw.get("logradouro") or raw.get("endereco")),
        "endereco_numero": _normalize_text(raw.get("numero")),
        "endereco_complemento": _normalize_text(raw.get("complemento")),
        "bairro": _normalize_text(raw.get("bairro")),
        "municipio": _normalize_text(raw.get("municipio") or raw.get("cidade")),
        "uf": _normalize_text(raw.get("uf") or raw.get("estado")).upper()[:2],
        "nome_fantasia": fantasia,
        "responsavel_legal": owner,
        "nicho": niche,
        "cnae": cnae_code,
        "cnae_descricao": cnae_description,
        "quantidade_socios": "1",
        "socio_1": owner,
        "cpf_socio_1": partner.get("cpf", "") if owner else "",
        "email_socio_1": email,
        "email_login_gestor": email,
        "email_confirmacao_admin": email,
        "email_cobranca": email,
        "senha_acesso": generated,
    }
    return payload


def _fetch_json(url: str) -> dict | None:
    request = Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "OppiCRM/1.0"},
    )
    try:
        with urlopen(request, timeout=_TIMEOUT) as response:
            if getattr(response, "status", 200) != 200:
                return None
            raw = response.read().decode("utf-8", errors="replace")
    except (URLError, TimeoutError, OSError, ValueError):
        return None
    try:
        data = json.loads(raw)
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    status = _normalize_search_text(data.get("status"))
    if status and status not in {"ok", "200"}:
        return None
    if data.get("message") and not (data.get("razao_social") or data.get("nome") or data.get("cnpj")):
        return None
    return data


def lookup_cnpj(cnpj: str) -> dict[str, str]:
    digits = _cnpj_digits(cnpj)
    if len(digits) != 14:
        raise CnpjLookupError("Informe um CNPJ com 14 dígitos.")

    raw = _fetch_json(_BRASILAPI_URL.format(cnpj=digits)) or _fetch_json(_RECEITAWS_URL.format(cnpj=digits))
    if not raw:
        raise CnpjLookupError("Não encontrei esse CNPJ na Receita. Confira o número e tente de novo.")

    payload = registration_from_cnpj_payload(raw)
    if not payload.get("empresa"):
        raise CnpjLookupError("A consulta do CNPJ não trouxe o nome da empresa.")
    return payload
