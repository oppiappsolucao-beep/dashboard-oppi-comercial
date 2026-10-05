"""Lê um texto colado pelo vendedor e devolve os campos de Dados gerais."""
from __future__ import annotations

import re
import unicodedata
from datetime import date

_UFS = {
    "AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA", "MG", "MS", "MT",
    "PA", "PB", "PE", "PI", "PR", "RJ", "RN", "RO", "RR", "RS", "SC", "SE", "SP", "TO",
}

# "pular" fica de fora do cadastro (financeiro ou bloco da Receita que não vira campo).
_LABELS: tuple[tuple[str, str], ...] = (
    ("pular", "forma de pagamento"),
    ("pular", "data da situacao cadastral"),
    ("pular", "comprovante de inscricao e de situacao cadastral"),
    ("pular", "comprovante de inscricao"),
    ("pular", "atividades economicas secundarias"),
    ("pular", "atividade economica secundaria"),
    ("pular", "natureza juridica"),
    ("pular", "ente federativo"),
    ("capital", "capital social"),
    ("pular", "quantidade de funcionarios"),
    ("colaboradores", "quantidade de funcionarios"),
    ("colaboradores", "quantidade de colaboradores"),
    ("colaboradores", "numero de funcionarios"),
    ("colaboradores", "numero de colaboradores"),
    ("data_abertura", "data de abertura"),
    ("data_abertura", "data abertura"),
    ("nome_contato", "nome do contato"),
    ("responsavel_legal", "responsavel legal"),
    ("responsavel_legal", "nome do responsavel"),
    ("nome_fantasia", "nome fantasia"),
    ("nome_fantasia", "nome de fantasia"),
    ("empresa_matriz", "empresa matriz"),
    ("empresa_matriz", "nome da matriz"),
    ("fantasia", "titulo do estabelecimento"),
    ("fantasia", "nome de fantasia"),
    ("nome_empresarial", "nome empresarial"),
    ("atividade", "atividade economica principal"),
    ("email", "endereco eletronico"),
    ("situacao", "situacao cadastral"),
    ("porte", "porte"),
    ("empresa", "razao social"),
    ("empresa", "nome da empresa"),
    ("telefone_alternativo", "telefone alternativo"),
    ("telefone_alternativo", "celular alternativo"),
    ("telefone_fixo", "telefone fixo"),
    ("telefone_b2b", "whatsapp"),
    ("telefone_b2b", "celular"),
    ("endereco_complemento", "complemento"),
    ("endereco_numero", "numero"),
    ("is_filial", "e filial"),
    ("is_filial", "filial"),
    ("empresa_matriz", "matriz"),
    ("empresa", "empresa"),
    ("cnpj", "cnpj"),
    ("nicho", "nicho"),
    ("nicho", "segmento"),
    ("nicho", "ramo"),
    ("colaboradores", "colaboradores"),
    ("colaboradores", "funcionarios"),
    ("data_abertura", "abertura"),
    ("data_abertura", "fundacao"),
    ("capital", "capital"),
    ("site", "website"),
    ("site", "site"),
    ("email", "e-mail"),
    ("email", "email"),
    ("telefone_b2b", "telefone"),
    ("telefone_b2b", "fone"),
    ("nome_contato", "contato"),
    ("responsavel_legal", "responsavel"),
    ("bairro", "bairro distrito"),
    ("cep", "cep"),
    ("endereco", "logradouro"),
    ("endereco", "endereco"),
    ("endereco", "rua"),
    ("endereco", "avenida"),
    ("bairro", "bairro"),
    ("municipio", "municipio"),
    ("municipio", "cidade"),
    ("uf", "estado"),
    ("uf", "uf"),
    ("instagram", "instagram"),
    ("linkedin", "linkedin"),
    ("observacoes", "observacoes"),
    ("observacoes", "obs"),
    ("socio_nome", "nome do socio"),
    ("socio_nome", "socio"),
    ("socio_telefone", "telefone do socio"),
    ("socio_cpf", "cpf do socio"),
    ("socio_cpf", "cpf"),
    ("socio_email", "email do socio"),
    ("pular", "vencimento"),
    ("pular", "mensalidade"),
    ("pular", "pagamento"),
    ("pular", "valor"),
    ("pular", "servico"),
    ("pular", "pix"),
    ("pular", "boleto"),
)

_FIELD_LABELS = {
    "empresa": "Empresa",
    "cnpj": "CNPJ",
    "is_filial": "Filial",
    "matriz_nome": "Empresa matriz",
    "nicho": "Nicho",
    "data_abertura": "Data de abertura",
    "capital": "Capital social",
    "colaboradores": "Colaboradores",
    "site": "Site",
    "email": "E-mail",
    "telefone_b2b": "WhatsApp",
    "nome_fantasia": "Nome fantasia",
    "data_fechamento": "Data de fechamento",
    "responsavel_legal": "Responsável legal",
    "nome_contato": "Nome do contato",
    "telefone_fixo": "Telefone fixo",
    "telefone_alternativo": "Telefone alternativo",
    "cep": "CEP",
    "endereco": "Endereço",
    "endereco_numero": "Número",
    "endereco_complemento": "Complemento",
    "bairro": "Bairro",
    "municipio": "Município",
    "uf": "UF",
    "instagram": "Instagram",
    "linkedin": "LinkedIn",
    "observacoes": "Observações",
    "quantidade_socios": "Sócios",
    "socio_1": "Sócio 1",
    "telefone_socio_1": "Telefone do sócio 1",
    "cpf_socio_1": "CPF do sócio 1",
    "email_socio_1": "E-mail do sócio 1",
    "email_login_gestor": "E-mail de login",
    "senha_acesso": "Senha de acesso",
    "socio_2": "Sócio 2",
    "telefone_socio_2": "Telefone do sócio 2",
    "cpf_socio_2": "CPF do sócio 2",
    "socio_3": "Sócio 3",
    "telefone_socio_3": "Telefone do sócio 3",
    "cpf_socio_3": "CPF do sócio 3",
}

_CNPJ_RE = re.compile(r"\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}")
_CPF_RE = re.compile(r"\d{3}\.?\d{3}\.?\d{3}-?\d{2}")
_EMAIL_RE = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.I)
_CEP_RE = re.compile(r"\b(\d{2})\.?(\d{3})-?(\d{3})\b")
_CNAE_RE = re.compile(r"^\d{2}\.\d{2}-\d-\d{2}\s*-\s*")
_DATE_RE = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b")
_PHONE_RE = re.compile(r"(?:\+?55\s*)?(?:\(?\d{2}\)?\s*)?\d{4,5}[-\s]?\d{4}")
_GREETING = {"bom dia", "boa tarde", "boa noite", "ola", "oi", "segue", "seguem", "dados"}


def _plain(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = text.encode("ascii", "ignore").decode("ascii")
    return " ".join(text.replace("_", " ").lower().split())


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def _format_cnpj(value: str) -> str:
    digits = _digits(value)[:14]
    if len(digits) != 14:
        return value.strip()
    return f"{digits[:2]}.{digits[2:5]}.{digits[5:8]}/{digits[8:12]}-{digits[12:]}"


def _format_cpf(value: str) -> str:
    digits = _digits(value)[:11]
    if len(digits) != 11:
        return value.strip()
    return f"{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:]}"


def _format_phone(value: str) -> str:
    digits = _digits(value)
    if digits.startswith("55") and len(digits) in {12, 13}:
        digits = digits[2:]
    if len(digits) == 11:
        return f"({digits[:2]}) {digits[2:7]}-{digits[7:]}"
    if len(digits) == 10:
        return f"({digits[:2]}) {digits[2:6]}-{digits[6:]}"
    return value.strip()


def _format_cep(value: str) -> str:
    match = _CEP_RE.search(value or "")
    if not match:
        return ""
    return f"{match.group(1)}{match.group(2)}-{match.group(3)}"


def _blank(value: str) -> bool:
    text = (value or "").strip()
    plain = _plain(text)
    if not plain:
        return True
    if set(text) <= {"*"}:
        return True
    return plain in {"nao informada", "nao informado", "nao consta", "sem informacao"}


def _activity_text(value: str) -> str:
    return _CNAE_RE.sub("", value or "").strip() or (value or "").strip()


def _yes(value: str) -> bool:
    text = _plain(value)
    return text in {"sim", "s", "yes", "filial", "1", "true", "x"}


def _match_label(label: str) -> str:
    plain = _plain(label).rstrip(" .").replace("/", " ")
    if not plain:
        return ""
    best = ""
    best_len = 0
    for field, alias in _LABELS:
        if plain == alias or plain.startswith(alias + " ") or plain.startswith(alias + "("):
            if len(alias) > best_len:
                best, best_len = field, len(alias)
        elif len(alias) >= 12 and alias in plain and len(alias) > best_len:
            best, best_len = field, len(alias)
    return best


def _split_labeled(line: str) -> tuple[str, str] | None:
    if ":" in line or ";" in line:
        left, right = re.split(r"\s*[:;]\s*", line, maxsplit=1)
        field = _match_label(left)
        if field:
            return field, right.strip()
    match = re.match(r"^(.{2,40}?)\s+-\s+(.+)$", line)
    if match:
        field = _match_label(match.group(1))
        if field:
            return field, match.group(2).strip()
    return None


def _match_niche(value: str, options: list[str]) -> str:
    wanted = _plain(value)
    if not wanted:
        return ""
    for option in options:
        if _plain(option) == wanted:
            return option
    for option in options:
        if wanted in _plain(option) or _plain(option) in wanted:
            return option
    return ""


def _take_uf(value: str) -> str:
    text = (value or "").strip().upper()
    if text in _UFS:
        return text
    match = re.search(r"(?:/|-)\s*([A-Z]{2})\b", text)
    if match and match.group(1) in _UFS:
        return match.group(1)
    tokens = re.findall(r"\b[A-Z]{2}\b", text)
    for token in reversed(tokens):
        if token in _UFS:
            return token
    return ""


def _split_address(value: str, fields: dict[str, str]) -> None:
    text = value.strip()
    if not text:
        return
    cep = _format_cep(text)
    if cep and not fields.get("cep"):
        fields["cep"] = cep
        text = _CEP_RE.sub("", text).strip(" ,;-")
    uf = _take_uf(text)
    if uf and not fields.get("uf"):
        fields["uf"] = uf
        text = re.sub(rf"(?:/|-)\s*{uf}\b", "", text, flags=re.I).strip(" ,;-")
    parts = [part.strip(" ,;-") for part in text.split(",") if part.strip(" ,;-")]
    if not parts:
        return
    street = parts[0]
    number = ""
    number_match = re.match(r"^(.*?)[,\s]+(?:n[ºo°.]?\s*)?(\d+[A-Za-z0-9/-]*)$", street)
    if number_match and not fields.get("endereco_numero"):
        street = number_match.group(1).strip(" ,;-")
        number = number_match.group(2)
    elif len(parts) > 1 and re.fullmatch(r"\d+[A-Za-z0-9/-]*", parts[1]) and not fields.get("endereco_numero"):
        number = parts[1]
        parts = [parts[0], *parts[2:]]
    if street and not fields.get("endereco"):
        fields["endereco"] = street
    if number and not fields.get("endereco_numero"):
        fields["endereco_numero"] = number
    rest = [part for part in parts[1:] if part != number]
    if rest and not fields.get("endereco_complemento") and re.search(r"sala|apto|apart|bloco|loja|conj", _plain(rest[0])):
        fields["endereco_complemento"] = rest.pop(0)
    if rest and not fields.get("bairro"):
        fields["bairro"] = rest.pop(0)
    if rest and not fields.get("municipio"):
        city = rest.pop(0)
        city_uf = _take_uf(city)
        if city_uf and not fields.get("uf"):
            fields["uf"] = city_uf
            city = re.sub(rf"(?:/|-)\s*{city_uf}\b", "", city, flags=re.I).strip(" ,;-")
        if city:
            fields["municipio"] = city


def _assign_partner(bucket: dict[str, list[str]], kind: str, value: str) -> None:
    if not value:
        return
    items = bucket.setdefault(kind, [])
    if len(items) < 3 and value not in items:
        items.append(value)


def _looks_password(value: str) -> bool:
    text = (value or "").strip()
    if " " in text or len(text) < 6:
        return False
    digits = _digits(text)
    if digits == text and len(digits) in {11, 14}:
        return False
    if _CNPJ_RE.fullmatch(text):
        return False
    return any(char.isalpha() for char in text) and any(char.isdigit() for char in text)


def _prefer_name(current: str, new: str) -> str:
    if not new:
        return current
    if not current:
        return new
    if current.isupper() and not new.isupper():
        return new
    return current


def _put_phone(fields: dict[str, str], value: str, *, alternate: bool = False) -> None:
    found = _PHONE_RE.search(value or "")
    phone = _format_phone(found.group(0) if found else value)
    digits = _digits(phone)
    if len(digits) not in {10, 11}:
        return
    current = _digits(fields.get("telefone_b2b", ""))
    if not alternate and not current:
        fields["telefone_b2b"] = phone
        return
    if digits != current and not fields.get("telefone_alternativo"):
        fields["telefone_alternativo"] = phone


def _apply_field(
    field: str,
    value: str,
    fields: dict[str, str],
    partners: dict[str, list[str]],
    extra: dict,
    niches: list[str],
) -> None:
    if field == "pular":
        if value and _match_label(value) == "" and any(word in _plain(value) for word in ("valor", "pagamento", "pix", "boleto")):
            extra["financial"] = True
        return
    if _blank(value):
        return
    if field == "is_filial":
        if _yes(value):
            fields["is_filial"] = "1"
        return
    if field == "empresa_matriz":
        fields["matriz_nome"] = value
        fields["is_filial"] = "1"
        return
    if field == "fantasia":
        extra["fantasia"] = value
        return
    if field == "nome_empresarial":
        extra["nome_empresarial"] = value
        return
    if field == "porte":
        extra["notes"].append(f"Porte: {value}")
        return
    if field == "atividade":
        extra["atividade"] = _activity_text(value)
        extra["notes"].append(f"Atividade: {extra['atividade']}")
        return
    if field == "responsavel_legal":
        fields["responsavel_legal"] = value
        _assign_partner(partners, "nome", value)
        return
    if field == "nome_fantasia":
        fields["nome_fantasia"] = value
        extra["fantasia"] = value
        return
    if field == "situacao":
        extra["notes"].append(f"Situação cadastral: {value}")
        return
    if field == "cnpj":
        found = _CNPJ_RE.search(value)
        fields["cnpj"] = _format_cnpj(found.group(0) if found else value)
        return
    if field in {"telefone_b2b", "telefone_fixo", "telefone_alternativo"}:
        if field == "telefone_b2b":
            _put_phone(fields, value)
        elif field not in fields:
            found = _PHONE_RE.search(value)
            fields[field] = _format_phone(found.group(0) if found else value)
        return
    if field == "email":
        found = _EMAIL_RE.search(value)
        if found and "email" not in fields:
            fields["email"] = found.group(0).lower()
        return
    if field == "cep":
        cep = _format_cep(value)
        if cep:
            fields["cep"] = cep
        return
    if field == "uf":
        uf = value.strip().upper()
        if uf in _UFS:
            fields["uf"] = uf
        return
    if field == "nicho":
        matched = _match_niche(value, niches)
        if matched:
            fields["nicho"] = matched
        return
    if field == "data_abertura":
        if "data_abertura" in fields:
            return
        found = _DATE_RE.search(value)
        fields["data_abertura"] = found.group(0) if found else value
        return
    if field == "endereco":
        if "," in value:
            _split_address(value, fields)
        elif "endereco" not in fields:
            fields["endereco"] = value
        return
    if field == "site":
        fields["site"] = value if value.lower().startswith(("http://", "https://")) or "." in value else value
        return
    if field == "socio_nome":
        _assign_partner(partners, "nome", value)
        return
    if field == "socio_telefone":
        found = _PHONE_RE.search(value)
        _assign_partner(partners, "telefone", _format_phone(found.group(0) if found else value))
        return
    if field == "socio_cpf":
        found = _CPF_RE.search(value)
        if found and len(_digits(found.group(0))) == 11:
            _assign_partner(partners, "cpf", _format_cpf(found.group(0)))
        return
    if field == "socio_email":
        found = _EMAIL_RE.search(value)
        if found:
            _assign_partner(partners, "email", found.group(0).lower())
        return
    if field not in fields:
        fields[field] = value


def _is_heading(line: str) -> bool:
    plain = _plain(line)
    if plain in {"matriz", "filial", "sede", "sem filial", "nao possui filial"}:
        return True
    return bool(_match_label(line))


def formulario_dados_gerais() -> dict:
    """Contrato do bot: só os campos de Dados gerais."""
    obrigatorios = {"empresa", "telefone_b2b"}
    return {
        "metodo": "POST",
        "caminho": "/cadastro/bot/dados-gerais",
        "autenticacao": "Vendedor logado no cadastro",
        "entrada": {"text": "Texto do formulário que o cliente preencheu e o vendedor colou"},
        "campos": [
            {"campo": key, "rotulo": label, "obrigatorio": key in obrigatorios}
            for key, label in _FIELD_LABELS.items()
        ],
        "fora_do_bot": "Financeiro fica com o comercial: serviço, valor, vencimento e forma de pagamento.",
    }


def ensure_login_fields(fields: dict) -> None:
    """Copia o e-mail da empresa para o login e gera a senha quando ainda estiver vazia."""
    email = str(
        fields.get("email")
        or fields.get("email_socio_1")
        or fields.get("email_login_gestor")
        or ""
    ).strip()
    if email:
        for key in (
            "email",
            "email_socio_1",
            "email_login_gestor",
            "email_confirmacao_admin",
            "email_cobranca",
        ):
            if not str(fields.get(key) or "").strip():
                fields[key] = email
    if not str(fields.get("senha_acesso") or "").strip():
        from app.services.cnpj_lookup import generate_access_password

        fields["senha_acesso"] = generate_access_password()


def read_dados_gerais(text: str, niche_options: list[str] | None = None) -> dict:
    """Extrai Dados gerais. Não devolve serviço, valor nem forma de pagamento."""
    raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    fields: dict[str, str] = {}
    partners: dict[str, list[str]] = {}
    extra = {"fantasia": "", "nome_empresarial": "", "notes": [], "financial": False}
    niches = list(niche_options or [])
    if not raw:
        return {"fields": {}, "preenchidos": [], "aviso": "Cole a mensagem do cliente antes de analisar."}

    lines = [" ".join(line.strip().split()) for line in raw.split("\n")]
    lines = [line for line in lines if line]
    loose: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        plain = _plain(line)
        labeled = _split_labeled(line)
        if labeled:
            _apply_field(labeled[0], labeled[1], fields, partners, extra, niches)
            index += 1
            continue
        if plain in {"matriz", "sede", "sem filial", "nao possui filial"}:
            fields.pop("is_filial", None)
            index += 1
            continue
        if plain == "filial":
            fields["is_filial"] = "1"
            index += 1
            continue
        heading = _match_label(line)
        if heading:
            value = ""
            if index + 1 < len(lines) and not _is_heading(lines[index + 1]):
                value = lines[index + 1]
                index += 2
            else:
                index += 1
            _apply_field(heading, value, fields, partners, extra, niches)
            continue
        loose.append(line)
        index += 1

    without_cnpj = _CNPJ_RE.sub(" ", raw)
    if "cnpj" not in fields:
        found = _CNPJ_RE.search(raw)
        if found:
            fields["cnpj"] = _format_cnpj(found.group(0))
    if "email" not in fields:
        found = _EMAIL_RE.search(raw)
        if found:
            fields["email"] = found.group(0).lower()
    if "cep" not in fields:
        cep = _format_cep(without_cnpj)
        if cep:
            fields["cep"] = cep
    if "telefone_b2b" not in fields:
        for found in _PHONE_RE.finditer(without_cnpj):
            _put_phone(fields, found.group(0))
            if fields.get("telefone_b2b"):
                break

    fantasia = extra["fantasia"]
    legal = extra["nome_empresarial"]
    if fantasia and not _blank(fantasia) and not fields.get("nome_fantasia"):
        fields["nome_fantasia"] = fantasia
    if legal and "empresa" not in fields:
        fields["empresa"] = legal
    elif not fields.get("empresa") and fantasia and not _blank(fantasia):
        fields["empresa"] = fantasia
    if legal and _plain(legal) != _plain(fields.get("empresa", "")) and not fields.get("nome_contato"):
        fields["nome_contato"] = legal

    role_next = False
    for line in loose:
        plain = _plain(line)
        if plain in {"proprietario", "socio administrador", "dono", "responsavel"}:
            role_next = True
            continue
        if _CNPJ_RE.search(line) or ( _digits(line) and len(_digits(line)) == 14):
            continue
        if _EMAIL_RE.fullmatch(line.strip()):
            continue
        if _looks_password(line):
            extra["notes"].append(f"Senha informada: {line.strip()}")
            continue
        if _PHONE_RE.search(line):
            _put_phone(fields, line, alternate=True)
            continue
        words = [part for part in line.split() if part]
        if len(words) >= 2 and not any(char.isdigit() for char in line) and plain not in _GREETING:
            if role_next or fields.get("empresa"):
                fields["nome_contato"] = _prefer_name(fields.get("nome_contato", ""), line.strip())
            elif "empresa" not in fields:
                fields["empresa"] = line.strip()
            role_next = False

    if extra["notes"]:
        note = ". ".join(extra["notes"])
        current = fields.get("observacoes", "")
        fields["observacoes"] = f"{current}. {note}".strip(". ") if current else note

    for index, name in enumerate(partners.get("nome") or [], start=1):
        fields[f"socio_{index}"] = name
    for index, phone in enumerate(partners.get("telefone") or [], start=1):
        fields[f"telefone_socio_{index}"] = phone
    for index, document in enumerate(partners.get("cpf") or [], start=1):
        fields[f"cpf_socio_{index}"] = document
    for index, email in enumerate(partners.get("email") or [], start=1):
        fields[f"email_socio_{index}"] = email
    partner_count = max((len(items) for items in partners.values()), default=0)
    if partner_count:
        fields["quantidade_socios"] = str(min(partner_count, 3))
    if fields.get("responsavel_legal") and not fields.get("socio_1"):
        fields["socio_1"] = fields["responsavel_legal"]
        fields["quantidade_socios"] = fields.get("quantidade_socios") or "1"
    if fields.get("socio_1") and not fields.get("responsavel_legal"):
        fields["responsavel_legal"] = fields["socio_1"]
    if extra.get("atividade") and not fields.get("nicho"):
        from app.services.cnpj_lookup import niche_from_cnae

        niche = niche_from_cnae("", extra.get("atividade") or "")
        if niche:
            fields["nicho"] = niche
    if not fields.get("data_fechamento"):
        fields["data_fechamento"] = date.today().isoformat()
    ensure_login_fields(fields)

    filled = [label for key, label in _FIELD_LABELS.items() if fields.get(key)]
    if not filled:
        aviso = "Não encontrei dados de cliente nesse texto."
    elif extra["financial"]:
        aviso = "Dados gerais preenchidos. Valor e pagamento ficam para você no Financeiro."
    else:
        aviso = "Dados gerais preenchidos. Confira os campos antes de salvar."
    return {"fields": fields, "preenchidos": filled, "aviso": aviso}
