"""Cliente HTTP da Evolution API (WhatsApp)."""
from __future__ import annotations

import logging
import time
from typing import Any
from urllib.parse import quote

import requests

from app.config import settings
from app.services.legacy_core import normalize_digits, normalize_text

logger = logging.getLogger(__name__)

# Evita fetchInstances (HTTP lento) em todo clique de filtro/linha no Atendimentos.
_INSTANCE_ROWS_CACHE: list[dict] | None = None
_INSTANCE_ROWS_CACHE_AT: float = 0.0
_INSTANCE_ROWS_TTL_SEC = 120.0


class EvolutionClientError(RuntimeError):
    pass


def is_configured() -> bool:
    return settings.evolution_configured


def _headers() -> dict[str, str]:
    return {
        "apikey": settings.evolution_api_key,
        "Content-Type": "application/json",
    }


def _url(path: str) -> str:
    base = settings.evolution_api_url.rstrip("/")
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{base}{path}"


def _instance_name() -> str:
    name = normalize_text(settings.evolution_primary_instance) or normalize_text(
        (settings.evolution_instances or [""])[0] if settings.evolution_instances else ""
    )
    if not name:
        # fallback: primeira parte se env ainda for string crua
        raw = normalize_text(settings.evolution_instance).split(",")[0].strip()
        name = raw
    if not name:
        raise EvolutionClientError("EVOLUTION_INSTANCE não configurada.")
    return name


def configured_instance_names() -> list[str]:
    return list(settings.evolution_instances or [])


def match_configured_instance(name: str) -> str:
    """Resolve nome de instância contra a lista configurada (case-insensitive)."""
    wanted = normalize_text(name)
    if not wanted:
        return _instance_name()
    for configured in configured_instance_names():
        if configured.lower() == wanted.lower():
            return configured
    for configured in configured_instance_names():
        if wanted.lower() in configured.lower() or configured.lower() in wanted.lower():
            return configured
    return wanted


def fetch_instance_names() -> list[str]:
    names: list[str] = []
    for item in _iter_instance_rows():
        nested = item.get("instance") if isinstance(item.get("instance"), dict) else {}
        candidate = (
            item.get("name")
            or item.get("instanceName")
            or item.get("instanceId")
            or nested.get("instanceName")
            or nested.get("name")
            or ""
        )
        candidate = normalize_text(candidate)
        if candidate and candidate not in names:
            names.append(candidate)
    return names


def _iter_instance_rows(*, force: bool = False, allow_network: bool = True) -> list[dict]:
    global _INSTANCE_ROWS_CACHE, _INSTANCE_ROWS_CACHE_AT
    now = time.monotonic()
    if (
        not force
        and _INSTANCE_ROWS_CACHE is not None
        and (now - _INSTANCE_ROWS_CACHE_AT) < _INSTANCE_ROWS_TTL_SEC
    ):
        return _INSTANCE_ROWS_CACHE
    if not allow_network:
        return list(_INSTANCE_ROWS_CACHE or [])
    try:
        response = requests.get(_url("/instance/fetchInstances"), headers=_headers(), timeout=8)
    except requests.RequestException:
        return list(_INSTANCE_ROWS_CACHE or [])
    if response.status_code >= 400:
        return list(_INSTANCE_ROWS_CACHE or [])
    data = _parse_json(response)
    rows = data if isinstance(data, list) else data.get("data") or data.get("instances") or []
    if isinstance(data, dict) and not rows and (data.get("name") or data.get("instanceName")):
        rows = [data]
    if not isinstance(rows, list):
        return list(_INSTANCE_ROWS_CACHE or [])
    parsed = [item for item in rows if isinstance(item, dict)]
    _INSTANCE_ROWS_CACHE = parsed
    _INSTANCE_ROWS_CACHE_AT = now
    return parsed


def get_instance_owner_phone(instance: str = "", *, allow_network: bool = True) -> str:
    """Número do WhatsApp conectado na instância (ownerJid/number)."""
    # Sem rede: só cache (filtros HTMX). Com rede: resolved_instance_name pode
    # chamar Evolution — evite em hot path de UI.
    if allow_network:
        try:
            wanted = resolved_instance_name(instance).lower()
        except Exception:
            wanted = match_configured_instance(
                instance or settings.evolution_primary_instance
            ).lower()
    else:
        wanted = match_configured_instance(
            instance or settings.evolution_primary_instance
        ).lower()
    for item in _iter_instance_rows(allow_network=allow_network):
        nested = item.get("instance") if isinstance(item.get("instance"), dict) else {}
        name = normalize_text(
            item.get("name")
            or item.get("instanceName")
            or nested.get("instanceName")
            or nested.get("name")
            or ""
        ).lower()
        if wanted and name and name != wanted and wanted not in name and name not in wanted:
            continue
        for candidate in (
            item.get("ownerJid"),
            item.get("owner"),
            item.get("number"),
            nested.get("ownerJid"),
            nested.get("owner"),
            nested.get("number"),
            item.get("ownerJid") if not nested else None,
        ):
            text = normalize_text(candidate or "")
            if not text:
                continue
            digits = normalize_phone_from_jid(text) if "@" in text else normalize_digits(text)
            if digits and len(digits) >= 10:
                if not digits.startswith("55") and len(digits) >= 10:
                    digits = f"55{digits}"
                return digits
        if wanted and name == wanted:
            break
    return ""


def is_self_chat(phone: str, jid: str = "", *, instance: str = "") -> bool:
    """True se o destino é o mesmo WhatsApp conectado na Evolution (na linha indicada)."""
    owner = get_instance_owner_phone(instance)
    if not owner:
        # fallback: qualquer linha configurada
        for name in configured_instance_names() or [_instance_name()]:
            owner = get_instance_owner_phone(name)
            if owner:
                break
    if not owner:
        return False
    target = _plain_phone(phone) or normalize_phone_from_jid(jid)
    return bool(target and _phone_tail_match(owner, target))


def resolved_instance_name(preferred: str = "") -> str:
    configured = match_configured_instance(preferred) if preferred else _instance_name()
    names = fetch_instance_names()
    if not names:
        return configured
    if configured in names:
        return configured
    lower = configured.lower()
    for name in names:
        if name.lower() == lower:
            return name
    # match parcial (ex.: configurado "Oppi" e existe "Oppi Comercial")
    for name in names:
        if lower in name.lower() or name.lower() in lower:
            return name
    # Se preferred era explícito e não achou nas disponíveis, ainda tenta o nome
    if preferred:
        return configured
    available = ", ".join(names)
    raise EvolutionClientError(
        f"Instância '{configured}' não encontrada na Evolution. "
        f"Disponíveis: {available}. "
        "Ajuste EVOLUTION_INSTANCE no Easypanel."
    )


def _instance_urls(segment: str, *, instance: str = "") -> list[str]:
    """Gera URLs com nome da instância encoded e raw (alguns proxies diferem)."""
    name = resolved_instance_name(instance)
    encoded = quote(name, safe="")
    paths = [f"{segment}/{encoded}"]
    if encoded != name:
        paths.append(f"{segment}/{name}")
    # também tenta o nome configurado cru, se diferente
    configured = match_configured_instance(instance) if instance else _instance_name()
    if configured != name:
        paths.append(f"{segment}/{quote(configured, safe='')}")
    return [_url(p) for p in paths]


def _parse_json(response: requests.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        data = {"raw": response.text}
    if isinstance(data, dict):
        return data
    return {"data": data}


def _response_looks_like_error(data: dict[str, Any]) -> str:
    status = normalize_text(data.get("status") or "").lower()
    if status in {"error", "unauthorized", "forbidden", "not found", "404"}:
        return str(data.get("message") or data.get("error") or data)
    if data.get("error"):
        return str(data.get("message") or data.get("error"))
    # formato comum: {"status":404,"error":"Not Found","response":{"message":[...]}}
    nested = data.get("response")
    if isinstance(nested, dict) and nested.get("message"):
        msg = nested.get("message")
        if isinstance(msg, list):
            return "; ".join(str(x) for x in msg)
        return str(msg)
    return ""


def extract_message_status(response: dict | None) -> str:
    data = response or {}
    for candidate in (
        data.get("status"),
        (data.get("message") or {}).get("status") if isinstance(data.get("message"), dict) else None,
        (data.get("data") or {}).get("status") if isinstance(data.get("data"), dict) else None,
        (data.get("key") or {}).get("status") if isinstance(data.get("key"), dict) else None,
    ):
        if candidate is None:
            continue
        text = normalize_text(candidate).upper()
        if text:
            return text
    return ""


def is_delivery_pending(response: dict | None) -> bool:
    status = extract_message_status(response)
    return status in {"PENDING", "ERROR", "0"} or status == ""


def extract_message_id(response: dict | None) -> str:
    data = response or {}
    stack = [data]
    seen = 0
    while stack and seen < 30:
        seen += 1
        cur = stack.pop(0)
        if not isinstance(cur, dict):
            continue
        for candidate in ("id", "messageId", "message_id"):
            value = cur.get(candidate)
            if value and candidate != "instance" and len(str(value)) >= 6:
                # evita pegar ids genéricos demais; ids WA costumam ser longos
                text = normalize_text(value)
                if text and text.lower() not in {"open", "close", "connected"}:
                    # key.id do WhatsApp
                    if cur.get("fromMe") is not None or cur.get("remoteJid") or candidate.startswith("message"):
                        return text
        key = cur.get("key")
        if isinstance(key, dict) and key.get("id"):
            return normalize_text(key.get("id"))
        for child_key in ("data", "message", "key", "response"):
            child = cur.get(child_key)
            if isinstance(child, dict):
                stack.append(child)
            elif isinstance(child, list):
                stack.extend([x for x in child if isinstance(x, dict)])
    # fallback: qualquer key.id
    key = data.get("key") if isinstance(data.get("key"), dict) else None
    if key and key.get("id"):
        return normalize_text(key.get("id"))
    nested = data.get("data") if isinstance(data.get("data"), dict) else None
    if nested:
        key = nested.get("key") if isinstance(nested.get("key"), dict) else None
        if key and key.get("id"):
            return normalize_text(key.get("id"))
    return ""


def phone_match_variants(phone: str) -> list[str]:
    """Variantes BR com/sem o 9º dígito — evita conversa duplicada e miss de match."""
    digits = normalize_digits(phone)
    if not digits:
        return []
    if not digits.startswith("55") and len(digits) >= 10:
        digits = f"55{digits}"
    variants = [digits]
    if digits.startswith("55") and len(digits) == 13 and digits[4] == "9":
        variants.append(digits[:4] + digits[5:])
    elif digits.startswith("55") and len(digits) == 12:
        variants.append(digits[:4] + "9" + digits[4:])
    return list(dict.fromkeys(v for v in variants if v))


def normalize_phone_from_jid(jid: str) -> str:
    raw = normalize_text(jid)
    if "@" in raw:
        raw = raw.split("@", 1)[0]
    if ":" in raw:
        raw = raw.split(":", 1)[0]
    digits = normalize_digits(raw)
    if digits.startswith("55") and len(digits) in (12, 13):
        return digits
    if len(digits) >= 10:
        return f"55{digits}"
    return digits


def is_placeholder_whatsapp_phone(value: str) -> bool:
    """Telefone sintético wa:… — não é destino válido na Evolution."""
    text = normalize_text(value).lower()
    return text.startswith("wa:") or text.startswith("lid:")


# DDDs brasileiros válidos (Anatel) — rejeita lixo tipo 5530… vindo de grupo.
_BR_VALID_DDDS = {
    11, 12, 13, 14, 15, 16, 17, 18, 19,
    21, 22, 24, 27, 28,
    31, 32, 33, 34, 35, 37, 38,
    41, 42, 43, 44, 45, 46, 47, 48, 49,
    51, 53, 54, 55,
    61, 62, 63, 64, 65, 66, 67, 68, 69,
    71, 73, 74, 75, 77, 79,
    81, 82, 83, 84, 85, 86, 87, 88, 89,
    91, 92, 93, 94, 95, 96, 97, 98, 99,
}


def is_valid_br_whatsapp_phone(value: str) -> bool:
    """Celular/fixo BR com DDD real (evita ID de grupo virando 55…)."""
    digits = normalize_digits(value)
    if digits.startswith("55") and len(digits) >= 12:
        digits = digits[2:]
    if len(digits) not in (10, 11):
        return False
    try:
        ddd = int(digits[:2])
    except ValueError:
        return False
    if ddd not in _BR_VALID_DDDS:
        return False
    # Celular 11 dígitos começa com 9; fixo 10 dígitos. Aceita JID @s.whatsapp.net
    # mesmo com dígitos "estranhos" se o DDD for válido — senão LID/PN reais
    # caem em invalid_identity e somem da inbox.
    if len(digits) == 11 and digits[2] != "9":
        return False
    return True


def is_usable_whatsapp_identity(phone: str = "", remote_jid: str = "") -> bool:
    """True se há telefone BR válido ou JID (@lid / @s.whatsapp.net) para inbox/envio."""
    if is_placeholder_whatsapp_phone(phone):
        return False
    jid = normalize_text(remote_jid).lower()
    if jid and is_whatsapp_group_jid(jid):
        return False
    if jid and ("@lid" in jid or "@s.whatsapp.net" in jid or "@c.us" in jid):
        left = jid.split("@", 1)[0]
        # @lid precisa ter id; PN precisa parecer telefone
        if "@lid" in jid:
            return bool(normalize_digits(left)) and len(normalize_digits(left)) >= 6
        digits = normalize_phone_from_jid(jid)
        if digits and is_valid_br_whatsapp_phone(digits):
            return True
        # Fallback: JID individual com dígitos suficientes (não descartar inbound)
        return bool(normalize_digits(left)) and len(normalize_digits(left)) >= 10
    if phone and is_valid_br_whatsapp_phone(phone):
        return True
    # remote_jid às vezes vem só com dígitos (sem @)
    if jid and "@" not in jid and is_valid_br_whatsapp_phone(jid):
        return True
    if phone and len(normalize_digits(phone)) >= 10:
        return True
    return False

def is_whatsapp_group_jid(value: str) -> bool:
    """True para grupos/broadcast — não entram no inbox de leads.

    Linked ID (@lid) é contato individual — nunca tratar como grupo
    (IDs longos sem 55 eram confundidos com id de grupo e sumiam do webhook).
    """
    text = normalize_text(value).lower()
    if not text:
        return False
    if "@lid" in text:
        return False
    if text.startswith("wa:"):
        return False
    if "@g.us" in text or text.endswith("@broadcast") or "status@broadcast" in text:
        return True
    # ID numérico típico de grupo (ex.: 1203630...), sem ser celular BR (55…)
    digits = normalize_digits(text.split("@", 1)[0])
    if digits.startswith("120") and len(digits) >= 15:
        return True
    if len(digits) >= 17 and not digits.startswith("55"):
        return True
    # NÃO tratar DDD inválido como grupo aqui — remoteJidAlt lixo derrubava DM 1:1.
    return False


def conversation_looks_like_group(
    *,
    remote_jid: str = "",
    phone_e164: str = "",
    contact_name: str = "",
) -> bool:
    # NÃO usar heurística de nome (apagava "Oppi Equipe" por engano).
    _ = contact_name
    return is_whatsapp_group_jid(remote_jid) or is_whatsapp_group_jid(phone_e164)


def message_looks_like_group(key: dict | None = None, item: dict | None = None) -> bool:
    """Só o remoteJid principal decide grupo — alt/participant não podem derrubar DM."""
    key = key if isinstance(key, dict) else {}
    item = item if isinstance(item, dict) else {}
    if item.get("isGroup") is True or key.get("isGroup") is True:
        return True
    remote = normalize_text(key.get("remoteJid") or item.get("remoteJid") or "")
    if not remote:
        return False
    # Linked ID = contato individual
    if "@lid" in remote.lower():
        return False
    return is_whatsapp_group_jid(remote)


def resolve_contact_identity(key: dict | None, item: dict | None = None) -> tuple[str, str]:
    """
    Retorna (phone_e164, remote_jid_para_envio).

    WhatsApp/Evolution às vezes manda @lid em remoteJid e o número real em remoteJidAlt.
    Para entrega 1:1, priorizamos o @lid (PN JID costuma ficar PENDING no Baileys atual).
    Grupos (@g.us) retornam ("", "") — inbox só aceita leads individuais.
    """
    key = key if isinstance(key, dict) else {}
    item = item if isinstance(item, dict) else {}

    if message_looks_like_group(key, item):
        return "", ""

    remote_jid = normalize_text(key.get("remoteJid") or item.get("remoteJid") or "")
    remote_alt = normalize_text(
        key.get("remoteJidAlt")
        or item.get("remoteJidAlt")
        or key.get("participant")
        or ""
    )
    sender_pn = normalize_text(
        key.get("senderPn")
        or item.get("senderPn")
        or item.get("sender")
        or key.get("participantPn")
        or item.get("participantPn")
        or item.get("cleanedNumber")
        or key.get("cleanedNumber")
        or ""
    )

    # Nunca use participant como identidade principal (é membro de grupo)
    if is_whatsapp_group_jid(remote_jid):
        return "", ""
    remote_alt_safe = remote_alt
    if is_whatsapp_group_jid(remote_alt) or (
        normalize_text(key.get("participant")) and remote_alt == normalize_text(key.get("participant"))
    ):
        # participant só importa em grupo — já filtrado acima; evita promover membro a lead
        if normalize_text(key.get("participant")) == remote_alt:
            remote_alt_safe = ""

    lid_jid = ""
    for candidate in (remote_jid, remote_alt_safe):
        if candidate and "@lid" in candidate.lower():
            lid_jid = candidate
            break

    phone = ""
    phone_jid = ""
    for candidate in (remote_alt_safe, sender_pn, remote_jid):
        if not candidate:
            continue
        lower = candidate.lower()
        if is_whatsapp_group_jid(candidate) or "broadcast" in lower:
            continue
        if "@lid" in lower:
            continue
        if "@s.whatsapp.net" in lower or "@c.us" in lower or "@" not in candidate:
            digits = normalize_phone_from_jid(candidate)
            if digits and len(digits) >= 10 and not is_whatsapp_group_jid(digits):
                phone = digits
                phone_jid = candidate if "@" in candidate else f"{digits}@s.whatsapp.net"
                break

    if not phone and remote_jid and "@lid" not in remote_jid.lower():
        if is_whatsapp_group_jid(remote_jid):
            return "", ""
        phone = normalize_phone_from_jid(remote_jid) or normalize_digits(remote_jid.split("@")[0])
        if phone and len(phone) >= 10 and not is_whatsapp_group_jid(phone):
            phone_jid = remote_jid if "@" in remote_jid else f"{phone}@s.whatsapp.net"
        else:
            phone = ""
            phone_jid = ""

    # @lid primeiro — necessário para entrega em várias versões Baileys/WhatsApp
    send_jid = lid_jid or phone_jid or remote_jid
    if is_whatsapp_group_jid(send_jid):
        return "", ""
    return phone, send_jid


def _prioritize_lid(targets: list[str]) -> list[str]:
    lids = [t for t in targets if "@lid" in t.lower()]
    others = [t for t in targets if "@lid" not in t.lower()]
    return list(dict.fromkeys(lids + others))


def _number_candidates(phone: str, jid: str = "") -> list[str]:
    out: list[str] = []
    jid = normalize_text(jid)
    number = normalize_digits(phone)

    # @lid sempre primeiro quando existir
    if jid and "@lid" in jid.lower():
        out.append(jid)

    if number:
        if not number.startswith("55") and len(number) >= 10:
            number = f"55{number}"
        out.append(number)

    if jid and "@lid" not in jid.lower():
        out.append(jid)
        if "@" in jid:
            left = jid.split("@", 1)[0]
            if left and left not in out:
                out.append(left)

    if number:
        out.append(f"{number}@s.whatsapp.net")
        out.append(f"{number}@c.us")
        if number.startswith("55") and len(number) == 12:
            with_nine = number[:4] + "9" + number[4:]
            out.append(with_nine)
            out.append(f"{with_nine}@s.whatsapp.net")
        if number.startswith("55") and len(number) == 13 and number[4] == "9":
            without_nine = number[:4] + number[5:]
            out.append(without_nine)
            out.append(f"{without_nine}@s.whatsapp.net")

    unique: list[str] = []
    for item in out:
        value = normalize_text(item)
        if value and value not in unique:
            unique.append(value)
    return _prioritize_lid(unique)


def enrich_targets_from_chats(phone: str, jid: str = "") -> list[str]:
    targets = _number_candidates(phone, jid)
    phone_digits = normalize_digits(phone)
    needle = normalize_text(jid)
    try:
        for url in _instance_urls("/chat/findChats"):
            response = requests.get(url, headers=_headers(), timeout=25)
            if response.status_code >= 400:
                continue
            data = _parse_json(response)
            chats = data if isinstance(data, list) else (
                data.get("data") or data.get("chats") or data.get("response") or []
            )
            if not isinstance(chats, list):
                continue
            matched: list[str] = []
            for chat in chats:
                if not isinstance(chat, dict):
                    continue
                cid = normalize_text(
                    chat.get("id")
                    or chat.get("remoteJid")
                    or _dig_chat_jid(chat)
                    or ""
                )
                if not cid or cid.endswith("@g.us") or "broadcast" in cid:
                    continue
                digits = normalize_digits(cid.split("@", 1)[0])
                if needle and (cid == needle or needle in cid or cid in needle):
                    matched.append(cid)
                elif phone_digits and "@lid" not in cid.lower() and (
                    (digits and phone_digits[-8:] == digits[-8:])
                    or phone_digits in cid
                ):
                    matched.append(cid)
                # Chat com @lid: só casa via telefone em remoteJidAlt (nunca pelos dígitos do lid)
                elif "@lid" in cid.lower() and phone_digits:
                    alt = normalize_text(
                        chat.get("remoteJidAlt")
                        or chat.get("owner")
                        or ""
                    )
                    alt_digits = normalize_digits(alt.split("@", 1)[0] if alt else "")
                    if alt_digits and phone_digits[-8:] == alt_digits[-8:]:
                        matched.append(cid)
            if matched:
                return _prioritize_lid(list(dict.fromkeys(matched + targets)))
            break
    except Exception as error:
        logger.warning("findChats falhou: %s", error)
    return targets


def _dig_chat_jid(chat: dict) -> str:
    for key in ("remoteJid", "jid", "chatId"):
        if chat.get(key):
            return normalize_text(chat.get(key))
    last = chat.get("lastMessage") if isinstance(chat.get("lastMessage"), dict) else {}
    key = last.get("key") if isinstance(last.get("key"), dict) else {}
    return normalize_text(key.get("remoteJid") or "")


def fetch_recent_chats(*, limit: int = 40, instance: str = "") -> list[dict]:
    """Lista chats 1:1 recentes da Evolution (para puxar conversas que o webhook perdeu)."""
    if not is_configured():
        return []
    limit = max(1, min(int(limit or 40), 80))
    last_error = ""

    def _parse_chats(data) -> list[dict]:
        chats = data if isinstance(data, list) else (
            data.get("data") or data.get("chats") or data.get("response") or []
            if isinstance(data, dict)
            else []
        )
        if not isinstance(chats, list):
            return []
        out: list[dict] = []
        for chat in chats:
            if not isinstance(chat, dict):
                continue
            cid = normalize_text(
                chat.get("id")
                or chat.get("remoteJid")
                or _dig_chat_jid(chat)
                or ""
            )
            if not cid or is_whatsapp_group_jid(cid):
                continue
            name = normalize_text(
                chat.get("pushName")
                or chat.get("name")
                or chat.get("notify")
                or ""
            )
            alt = normalize_text(
                chat.get("remoteJidAlt")
                or chat.get("owner")
                or ""
            )
            phone = ""
            for candidate in (alt, cid):
                if not candidate or "@lid" in candidate.lower():
                    continue
                digits = normalize_phone_from_jid(candidate)
                if digits and len(digits) >= 10 and not is_whatsapp_group_jid(digits):
                    phone = digits
                    break
            out.append({
                "remote_jid": cid,
                "phone_e164": phone,
                "contact_name": name,
                "evolution_instance": match_configured_instance(instance) if instance else _instance_name(),
            })
            if len(out) >= limit:
                break
        return out

    for url in _instance_urls("/chat/findChats", instance=instance):
        for method in ("post", "get"):
            try:
                if method == "post":
                    response = requests.post(
                        url, headers=_headers(), json={"limit": limit}, timeout=12
                    )
                else:
                    response = requests.get(url, headers=_headers(), timeout=12)
            except requests.RequestException as error:
                last_error = str(error)
                continue
            data = _parse_json(response)
            if response.status_code >= 400:
                last_error = _response_looks_like_error(data) or response.text[:200]
                continue
            parsed = _parse_chats(data)
            if parsed:
                return parsed
    if last_error:
        logger.warning("findChats falhou: %s", last_error)
    return []

def get_connection_state(instance: str = "") -> str:
    last_error = ""
    for url in _instance_urls("/instance/connectionState", instance=instance):
        try:
            response = requests.get(url, headers=_headers(), timeout=15)
        except requests.RequestException as error:
            last_error = str(error)
            continue
        data = _parse_json(response)
        if response.status_code >= 400:
            last_error = _response_looks_like_error(data) or response.text[:200]
            continue
        state = ""
        if isinstance(data.get("instance"), dict):
            state = normalize_text(data["instance"].get("state") or data["instance"].get("status"))
        state = state or normalize_text(data.get("state") or data.get("status"))
        return state.lower()
    if last_error:
        logger.warning("connectionState falhou: %s", last_error)
    return ""


def find_messages(remote_jid: str, *, limit: int = 30, instance: str = "") -> list[dict]:
    """Busca mensagens recentes no Evolution (fallback quando o webhook falha)."""
    jid = normalize_text(remote_jid)
    if not jid or not is_configured():
        return []
    payload = {
        "where": {"key": {"remoteJid": jid}},
        "page": 1,
        "offset": max(1, min(int(limit or 30), 80)),
    }
    last_error = ""
    # Só a 1ª URL e timeout curto — hydrate no webhook não pode travar a inbox.
    urls = list(_instance_urls("/chat/findMessages", instance=instance))[:1]
    for url in urls:
        try:
            response = requests.post(url, headers=_headers(), json=payload, timeout=3)
        except requests.RequestException as error:
            last_error = str(error)
            continue
        data = _parse_json(response)
        if response.status_code >= 400:
            last_error = _response_looks_like_error(data) or response.text[:200]
            continue
        records: list = []
        if isinstance(data.get("messages"), dict):
            records = data["messages"].get("records") or []
        elif isinstance(data.get("messages"), list):
            records = data["messages"]
        elif isinstance(data.get("data"), list):
            records = data["data"]
        elif isinstance(data, list):
            records = data
        if isinstance(records, list):
            return [item for item in records if isinstance(item, dict)]
    if last_error:
        logger.warning("findMessages falhou para %s: %s", jid, last_error)
    return []


def assert_instance_ready(instance: str = "") -> None:
    state = get_connection_state(instance)
    if not state:
        # não bloqueia se o endpoint não existir em algumas versões
        return
    if state not in {"open", "connected"}:
        raise EvolutionClientError(
            f"Instância Evolution não está conectada (estado: {state}). "
            "Reconecte o QR no Manager e tente de novo."
        )


def _plain_phone(phone: str) -> str:
    digits = normalize_digits(phone)
    if not digits:
        return ""
    if not digits.startswith("55") and len(digits) >= 10:
        digits = f"55{digits}"
    return digits


def _phone_tail_match(a: str, b: str) -> bool:
    da = normalize_digits(a)
    db = normalize_digits(b)
    if not da or not db:
        return False
    return da[-8:] == db[-8:] or da == db


def discover_lid_for_phone(phone: str, jid: str = "") -> str:
    """
    Tenta achar o @lid do contato.

    No Baileys atual, envio 1:1 via @lid costuma entregar; número puro/@s.whatsapp.net
    fica PENDING (erro 463 / tctoken).
    """
    jid = normalize_text(jid)
    if jid and "@lid" in jid.lower():
        return jid

    digits = _plain_phone(phone)
    if not digits and not jid:
        return ""

    # 1) Chats recentes da Evolution
    try:
        for chat in fetch_recent_chats(limit=80):
            cid = normalize_text(chat.get("remote_jid") or "")
            if not cid or "@lid" not in cid.lower():
                continue
            chat_phone = normalize_text(chat.get("phone_e164") or "")
            if digits and chat_phone and _phone_tail_match(digits, chat_phone):
                return cid
            # alguns chats só trazem alt no id — confere enrich
    except Exception as error:
        logger.warning("discover_lid findChats: %s", error)

    # 2) findChats bruto (casa remoteJidAlt com o telefone)
    try:
        for url in _instance_urls("/chat/findChats"):
            response = requests.get(url, headers=_headers(), timeout=20)
            if response.status_code >= 400:
                continue
            data = _parse_json(response)
            chats = data if isinstance(data, list) else (
                data.get("data") or data.get("chats") or data.get("response") or []
            )
            if not isinstance(chats, list):
                continue
            for chat in chats:
                if not isinstance(chat, dict):
                    continue
                cid = normalize_text(
                    chat.get("id") or chat.get("remoteJid") or _dig_chat_jid(chat) or ""
                )
                if not cid or "@lid" not in cid.lower():
                    continue
                alt = normalize_text(
                    chat.get("remoteJidAlt")
                    or chat.get("owner")
                    or chat.get("pn")
                    or ""
                )
                alt_digits = normalize_phone_from_jid(alt) if alt else ""
                if digits and alt_digits and _phone_tail_match(digits, alt_digits):
                    return cid
                if digits and _phone_tail_match(digits, cid.split("@", 1)[0]):
                    # raro, mas cobre lid numérico coincidente
                    continue
            break
    except Exception as error:
        logger.warning("discover_lid findChats raw: %s", error)

    # 3) Histórico de mensagens no JID de telefone — às vezes traz remoteJid=@lid
    probe_jids = []
    if jid:
        probe_jids.append(jid)
    if digits:
        probe_jids.append(f"{digits}@s.whatsapp.net")
    for probe in probe_jids:
        try:
            for item in find_messages(probe, limit=25):
                key = item.get("key") if isinstance(item.get("key"), dict) else {}
                for candidate in (
                    key.get("remoteJid"),
                    key.get("remoteJidAlt"),
                    key.get("participant"),
                    item.get("senderLid"),
                    item.get("remoteJid"),
                ):
                    text = normalize_text(candidate or "")
                    if text and "@lid" in text.lower():
                        return text
        except Exception as error:
            logger.warning("discover_lid findMessages %s: %s", probe, error)

    # 4) Endpoint de contatos (nem toda versão tem)
    try:
        for url in _instance_urls("/chat/findContacts"):
            response = requests.post(
                url,
                headers=_headers(),
                json={"where": {}},
                timeout=20,
            )
            if response.status_code >= 400:
                continue
            data = _parse_json(response)
            rows = data if isinstance(data, list) else (
                data.get("data") or data.get("contacts") or data.get("response") or []
            )
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                cid = normalize_text(row.get("id") or row.get("remoteJid") or "")
                if not cid or "@lid" not in cid.lower():
                    continue
                alt = normalize_text(row.get("remoteJidAlt") or row.get("pn") or "")
                if digits and alt and _phone_tail_match(digits, normalize_phone_from_jid(alt)):
                    return cid
            break
    except Exception as error:
        logger.warning("discover_lid findContacts: %s", error)

    return ""


def find_exact_chat_target(phone: str, jid: str = "") -> str:
    """Id do chat na Evolution (mesmo alvo do Manager). Match estrito por telefone."""
    digits = _plain_phone(phone)
    needle = normalize_text(jid)
    if needle and "@lid" in needle.lower():
        return needle

    for url in _instance_urls("/chat/findChats"):
        for use_post in (False, True):
            try:
                if use_post:
                    response = requests.post(url, headers=_headers(), json={"where": {}}, timeout=25)
                else:
                    response = requests.get(url, headers=_headers(), timeout=25)
            except requests.RequestException:
                continue
            if response.status_code >= 400:
                continue
            data = _parse_json(response)
            chats = data if isinstance(data, list) else (
                data.get("data") or data.get("chats") or data.get("response") or []
            )
            if not isinstance(chats, list):
                continue
            for chat in chats:
                if not isinstance(chat, dict):
                    continue
                cid = normalize_text(
                    chat.get("id") or chat.get("remoteJid") or _dig_chat_jid(chat) or ""
                )
                if not cid or is_whatsapp_group_jid(cid) or "broadcast" in cid.lower():
                    continue
                if needle and cid == needle:
                    return cid
                alt = normalize_text(
                    chat.get("remoteJidAlt") or chat.get("owner") or chat.get("pn") or ""
                )
                last = chat.get("lastMessage") if isinstance(chat.get("lastMessage"), dict) else {}
                last_key = last.get("key") if isinstance(last.get("key"), dict) else {}
                alt2 = normalize_text(
                    last_key.get("remoteJidAlt")
                    or last_key.get("participantPn")
                    or last.get("senderPn")
                    or ""
                )
                for alt_candidate in (alt, alt2):
                    if digits and alt_candidate:
                        alt_digits = normalize_phone_from_jid(alt_candidate)
                        if alt_digits and _phone_tail_match(digits, alt_digits):
                            return cid
                if "@lid" not in cid.lower() and digits:
                    cid_digits = normalize_phone_from_jid(cid)
                    if cid_digits and _phone_tail_match(digits, cid_digits):
                        return cid
    return ""


def resolve_number_via_whatsapp_check(phone: str) -> str:
    """Resolve JID como o Manager (POST /chat/whatsappNumbers)."""
    digits = _plain_phone(phone)
    if not digits:
        return ""
    for url in _instance_urls("/chat/whatsappNumbers"):
        try:
            response = requests.post(
                url, headers=_headers(), json={"numbers": [digits]}, timeout=20
            )
        except requests.RequestException:
            continue
        if response.status_code >= 400:
            continue
        data = _parse_json(response)
        rows = data if isinstance(data, list) else (
            data.get("data") or data.get("response") or data.get("numbers") or []
        )
        if isinstance(data, dict) and not rows and data.get("jid"):
            rows = [data]
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict) or row.get("exists") is False:
                continue
            jid = normalize_text(row.get("jid") or row.get("number") or "")
            if jid:
                return jid
            number = normalize_digits(row.get("number") or "")
            if number and len(number) >= 10:
                return number
    return ""


def send_typing_presence(number: str, *, delay_ms: int = 1200) -> None:
    payload = {"number": number, "options": {"presence": "composing", "delay": delay_ms}}
    for url in _instance_urls("/chat/sendPresence"):
        try:
            requests.post(url, headers=_headers(), json=payload, timeout=15)
            return
        except requests.RequestException:
            continue


def normalize_send_number(number: str) -> str:
    """
    Alvo de envio no formato que entrega.
    - @lid: mantém
    - @s.whatsapp.net / @c.us: vira só o número (Manager faz assim; JID PN fica PENDING)
    """
    text = normalize_text(number)
    if not text:
        return ""
    lower = text.lower()
    if "@lid" in lower:
        return text
    if "@g.us" in lower or "broadcast" in lower:
        return ""
    if "@" in text:
        return normalize_phone_from_jid(text) or normalize_digits(text.split("@", 1)[0])
    return _plain_phone(text) or text


def _send_target_candidates(phone: str, jid: str = "") -> list[str]:
    """Ordem: @lid → número puro. Nunca envia @s.whatsapp.net (fica PENDING)."""
    digits = _plain_phone(phone)
    ordered: list[str] = []

    def add(item: str) -> None:
        value = normalize_send_number(item)
        if value and value not in ordered:
            ordered.append(value)

    exact = find_exact_chat_target(phone, jid)
    if exact and "@lid" in exact.lower():
        add(exact)

    discovered = discover_lid_for_phone(phone, exact or jid)
    add(discovered)

    stored = normalize_text(jid)
    if stored and "@lid" in stored.lower():
        add(stored)

    # Número puro (não JID PN)
    add(digits)
    add(resolve_number_via_whatsapp_check(phone))
    if exact:
        add(exact)

    return ordered[:3]


def _pick_send_target(phone: str, jid: str = "") -> str:
    candidates = _send_target_candidates(phone, jid)
    return candidates[0] if candidates else ""


def _status_looks_delivered(status: str) -> bool:
    text = normalize_text(status).upper()
    return text in {
        "SERVER_ACK",
        "DELIVERY_ACK",
        "READ",
        "PLAYED",
        "SUCCESS",
        "SENT",
        "RECEIVED",
        "2",
        "3",
        "4",
    }


def send_text(phone: str, text: str, *, jid: str = "", instance: str = "") -> dict[str, Any]:
    if not is_configured():
        raise EvolutionClientError("Evolution API não configurada.")
    body = str(text or "").strip()
    if not body:
        raise EvolutionClientError("Mensagem vazia.")

    assert_instance_ready(instance)

    if is_self_chat(phone, jid, instance=instance):
        owner = get_instance_owner_phone(instance)
        raise EvolutionClientError(
            "Destino é o mesmo WhatsApp conectado na Evolution "
            f"({owner}). O WhatsApp não entrega mensagem para o próprio número. "
            "Teste enviando para outro celular (cliente real)."
        )

    # Caminho rápido (igual ao que já entregou 1x): @lid salvo OU número puro.
    # Evita findChats/presence/delay em todo envio — isso travava o chat após a 1ª msg.
    stored = normalize_text(jid)
    digits = _plain_phone(phone)
    candidates: list[str] = []
    if stored and "@lid" in stored.lower():
        candidates.append(stored)
    if digits:
        candidates.append(digits)
    if stored and "@lid" not in stored.lower():
        normalized = normalize_send_number(stored)
        if normalized and normalized not in candidates:
            candidates.append(normalized)
    if not candidates:
        raise EvolutionClientError("Telefone/JID da conversa inválido para envio.")

    urls = _instance_urls("/message/sendText", instance=instance)[:1] or _instance_urls(
        "/message/sendText", instance=instance
    )
    errors: list[str] = []

    for number in candidates:
        number = normalize_send_number(number)
        if not number:
            continue
        payload = {"number": number, "text": body}
        for url in urls:
            try:
                response = requests.post(url, json=payload, headers=_headers(), timeout=30)
            except requests.RequestException as error:
                errors.append(f"{number}: {error}")
                continue

            data = _parse_json(response)
            err = _response_looks_like_error(data)
            if response.status_code >= 400 or err:
                errors.append(
                    f"{number} HTTP {response.status_code}: {err or response.text[:180]}"
                )
                continue

            msg_id = extract_message_id(data)
            if not msg_id:
                errors.append(
                    f"{number}: Evolution respondeu sem ID de mensagem: {str(data)[:180]}"
                )
                continue

            status = extract_message_status(data) or "UNKNOWN"
            logger.info(
                "Evolution sendText instance=%s number=%s id=%s status=%s",
                resolved_instance_name(instance),
                number,
                msg_id,
                status,
            )
            data["_oppi_send_number"] = number
            data["_oppi_send_status"] = status
            data["_oppi_resolved_lid"] = number if "@lid" in number.lower() else ""
            data["_oppi_delivery_pending"] = False
            return data

    detail = " | ".join(errors[-4:]) if errors else "sem detalhes"
    raise EvolutionClientError(
        "Não foi possível enviar no WhatsApp via Evolution. "
        f"Instância={resolved_instance_name(instance)}. {detail}"
    )


def send_media(
    phone: str,
    *,
    media_url: str,
    media_type: str = "image",
    caption: str = "",
    filename: str = "",
    mimetype: str = "",
    jid: str = "",
    instance: str = "",
) -> dict[str, Any]:
    """Envia mídia via Evolution (image/document/audio)."""
    if not is_configured():
        raise EvolutionClientError("Evolution API não configurada.")
    assert_instance_ready(instance)
    number = _pick_send_target(phone, jid)
    if not number:
        raise EvolutionClientError("Telefone/JID da conversa inválido para envio.")
    mediatype = {
        "image": "image",
        "document": "document",
        "audio": "audio",
        "video": "video",
    }.get(media_type, "document")
    errors: list[str] = []
    payload = {
        "number": number,
        "mediatype": mediatype,
        "media": media_url,
        "caption": str(caption or "").strip(),
        "fileName": normalize_text(filename) or "arquivo",
    }
    if mimetype:
        payload["mimetype"] = mimetype
    for url in _instance_urls("/message/sendMedia", instance=instance):
        try:
            response = requests.post(url, json=payload, headers=_headers(), timeout=60)
        except requests.RequestException as error:
            errors.append(str(error))
            continue
        data = _parse_json(response)
        err = _response_looks_like_error(data)
        if response.status_code >= 400 or err:
            errors.append(err or response.text[:180])
            continue
        if extract_message_id(data):
            return data
        errors.append(f"sem ID: {str(data)[:160]}")
    raise EvolutionClientError(
        "Falha ao enviar mídia via Evolution. " + (" | ".join(errors[-3:]) if errors else "")
    )


def send_whatsapp_audio(
    phone: str,
    *,
    audio_base64: str,
    jid: str = "",
    mimetype: str = "audio/ogg",
    instance: str = "",
) -> dict[str, Any]:
    """Envia áudio como nota de voz (PTT) via Evolution sendWhatsAppAudio."""
    if not is_configured():
        raise EvolutionClientError("Evolution API não configurada.")
    assert_instance_ready(instance)
    number = _pick_send_target(phone, jid)
    if not number:
        raise EvolutionClientError("Telefone/JID da conversa inválido para envio.")
    raw = str(audio_base64 or "").strip()
    if not raw:
        raise EvolutionClientError("Áudio vazio.")
    mime = (mimetype or "audio/ogg").split(";")[0].strip() or "audio/ogg"
    # Aceita data URI ou base64 puro
    if raw.startswith("data:") and "," in raw:
        header, b64 = raw.split(",", 1)
        raw_b64 = b64.strip()
        if ";base64" in header and ":" in header:
            maybe_mime = header.split(":", 1)[1].split(";", 1)[0].strip()
            if maybe_mime.startswith("audio/"):
                mime = maybe_mime
    else:
        raw_b64 = raw
    if not raw_b64:
        raise EvolutionClientError("Áudio vazio.")

    data_uri = f"data:{mime};base64,{raw_b64}"
    # Ordem: formato que já funcionava → variantes de versões Evolution
    payloads: list[dict[str, Any]] = [
        {"number": number, "audio": raw_b64, "encoding": True},
        {"number": number, "audio": raw_b64},
        {"number": number, "audio": data_uri, "encoding": True},
        {
            "number": number,
            "options": {"encoding": True},
            "audioMessage": {"audio": raw_b64},
        },
        {
            "number": number,
            "options": {"encoding": True},
            "audioMessage": {"audio": data_uri},
        },
    ]

    errors: list[str] = []
    for payload in payloads:
        for url in _instance_urls("/message/sendWhatsAppAudio", instance=instance):
            try:
                response = requests.post(url, json=payload, headers=_headers(), timeout=90)
            except requests.RequestException as error:
                errors.append(str(error))
                continue
            data = _parse_json(response)
            err = _response_looks_like_error(data)
            if response.status_code >= 400 or err:
                detail = err or response.text[:180] or f"HTTP {response.status_code}"
                errors.append(detail)
                continue
            if extract_message_id(data):
                return data
            # Algumas versões devolvem 201/200 sem key.id — aceita se não parece erro
            if response.status_code < 400 and data:
                data.setdefault("_oppi_send_status", extract_message_status(data) or "UNKNOWN")
                return data
            errors.append(f"sem ID: {str(data)[:160]}")

    # Fallback: sendMedia como áudio (não PTT, mas entrega)
    try:
        return send_media(
            phone,
            media_url=raw_b64,
            media_type="audio",
            filename="audio.ogg" if "ogg" in mime else "audio.webm",
            mimetype=mime,
            jid=jid,
            instance=instance,
        )
    except EvolutionClientError as media_error:
        errors.append(str(media_error))

    raise EvolutionClientError(
        "Falha ao enviar áudio via Evolution. " + (" | ".join(errors[-4:]) if errors else "")
    )


def get_base64_from_media_message(
    message_id: str,
    *,
    remote_jid: str = "",
    from_me: bool | None = None,
    instance: str = "",
) -> dict[str, Any]:
    """Baixa mídia (áudio/imagem/…) via Evolution getBase64FromMediaMessage."""
    if not is_configured():
        raise EvolutionClientError("Evolution API não configurada.")
    msg_id = normalize_text(message_id)
    if not msg_id:
        raise EvolutionClientError("ID da mensagem Evolution vazio.")

    key: dict[str, Any] = {"id": msg_id}
    jid = normalize_text(remote_jid)
    if jid:
        key["remoteJid"] = jid
    if from_me is not None:
        key["fromMe"] = bool(from_me)

    payloads = [
        {"message": {"key": key}, "convertToMp4": False},
        {"message": {"key": {"id": msg_id}}, "convertToMp4": False},
        {"message": {"key": key}},
    ]
    errors: list[str] = []
    for payload in payloads:
        for url in _instance_urls("/chat/getBase64FromMediaMessage", instance=instance):
            try:
                response = requests.post(url, json=payload, headers=_headers(), timeout=90)
            except requests.RequestException as error:
                errors.append(str(error))
                continue
            data = _parse_json(response)
            err = _response_looks_like_error(data)
            if response.status_code >= 400 or err:
                errors.append(err or response.text[:160] or f"HTTP {response.status_code}")
                continue
            # Resposta pode vir aninhada
            nested = data.get("data") if isinstance(data.get("data"), dict) else data
            b64 = (
                normalize_text(nested.get("base64") or "")
                or normalize_text(data.get("base64") or "")
            )
            if b64.startswith("data:") and "," in b64:
                b64 = b64.split(",", 1)[1]
            if not b64:
                errors.append("resposta sem base64")
                continue
            mime = normalize_text(
                nested.get("mimetype") or nested.get("mimeType") or data.get("mimetype") or ""
            )
            filename = normalize_text(
                nested.get("fileName") or nested.get("filename") or data.get("fileName") or ""
            )
            return {
                "base64": b64,
                "mimetype": mime,
                "filename": filename,
                "mediaType": normalize_text(nested.get("mediaType") or data.get("mediaType") or ""),
            }
    raise EvolutionClientError(
        "Não foi possível baixar a mídia na Evolution. "
        + (" | ".join(errors[-3:]) if errors else "")
    )


def webhook_callback_url() -> str:
    """URL que a Evolution deve chamar (inclui token se configurado)."""
    base = (settings.public_app_url or "https://comercial.oppitech.com.br").rstrip("/")
    url = f"{base}/webhooks/evolution"
    token = normalize_text(settings.evolution_webhook_token)
    if token:
        url = f"{url}?token={quote(token)}"
    return url


def find_instance_webhook(instance: str = "") -> dict:
    """Lê a configuração atual do webhook na Evolution."""
    name = match_configured_instance(instance) if instance else _instance_name()
    if not name or not is_configured():
        return {"instance": name or "", "ok": False, "error": "not_configured"}
    last_error = ""
    for path in (
        f"/webhook/find/{quote(name, safe='')}",
        f"/webhook/{quote(name, safe='')}",
    ):
        try:
            response = requests.get(_url(path), headers=_headers(), timeout=10)
        except requests.RequestException as error:
            last_error = str(error)
            continue
        data = _parse_json(response)
        if response.status_code >= 400:
            last_error = (response.text or "")[:200] or f"HTTP {response.status_code}"
            continue
        return {"instance": name, "ok": True, "config": data}
    return {"instance": name, "ok": False, "error": last_error or "not_found"}


def ensure_instance_webhook(instance: str = "") -> dict:
    """Configura webhook MESSAGES_UPSERT na instância (Evolution v1/v2)."""
    name = match_configured_instance(instance) if instance else _instance_name()
    if not name or not is_configured():
        return {"instance": name or "", "ok": False, "error": "not_configured"}

    url = webhook_callback_url()
    events = [
        "MESSAGES_UPSERT",
        "MESSAGES_UPDATE",
        "MESSAGES_SET",
        "CONNECTION_UPDATE",
        "SEND_MESSAGE",
    ]
    payloads = (
        {
            "enabled": True,
            "url": url,
            "webhookByEvents": False,
            "webhookBase64": True,
            "events": events,
        },
        {
            "webhook": {
                "enabled": True,
                "url": url,
                "webhookByEvents": False,
                "webhookBase64": True,
                "events": events,
            }
        },
    )
    last_error = ""
    set_ok = False
    set_status = 0
    for path in (
        f"/webhook/set/{quote(name, safe='')}",
        f"/webhook/instance/{quote(name, safe='')}",
    ):
        for payload in payloads:
            try:
                response = requests.post(
                    _url(path),
                    headers=_headers(),
                    json=payload,
                    timeout=12,
                )
            except requests.RequestException as error:
                last_error = str(error)
                continue
            if response.status_code < 400:
                set_ok = True
                set_status = response.status_code
                logger.info(
                    "Evolution webhook ok instance=%s url=%s status=%s",
                    name,
                    url,
                    response.status_code,
                )
                break
            last_error = (response.text or "")[:200] or f"HTTP {response.status_code}"
        if set_ok:
            break

    found = find_instance_webhook(name)
    if set_ok:
        return {
            "instance": name,
            "ok": True,
            "status": set_status,
            "url": url,
            "found": found,
        }
    logger.warning(
        "Evolution webhook falhou instance=%s error=%s", name, last_error
    )
    return {
        "instance": name,
        "ok": False,
        "error": last_error,
        "url": url,
        "found": found,
    }


def ensure_webhooks_for_all_instances() -> list[dict]:
    """Garante webhook nas linhas configuradas em EVOLUTION_INSTANCE."""
    names = configured_instance_names() or ([_instance_name()] if _instance_name() else [])
    return [ensure_instance_webhook(name) for name in names if name]
