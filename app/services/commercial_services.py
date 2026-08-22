"""Serviços comerciais cadastrados em Configurações — controle interno (sem Asaas)."""
from __future__ import annotations

from app.services.app_settings import load_app_settings, save_app_settings
from app.services.legacy_core import normalize_text, parse_money


_LEGACY_DEFAULT_SERVICE_NAMES = frozenset({"oppi vision", "oppi flow", "oppi track"})


def _normalize_service_name(value: str) -> str:
    return normalize_text(value)


def _normalize_quantidade(value) -> int:
    raw = normalize_text(value).replace(".", "").replace(",", ".")
    if not raw:
        return 1
    try:
        qty = int(float(raw))
    except (TypeError, ValueError):
        return 1
    return max(qty, 1)


def _normalize_catalog_item(raw) -> dict | None:
    if isinstance(raw, str):
        name = _normalize_service_name(raw)
        if not name:
            return None
        return {"name": name, "valor": "", "quantidade": 1, "valor_num": 0.0}
    if not isinstance(raw, dict):
        return None
    name = _normalize_service_name(raw.get("name") or raw.get("servico") or raw.get("nome"))
    if not name:
        return None
    valor = normalize_text(raw.get("valor"))
    quantidade = _normalize_quantidade(raw.get("quantidade"))
    return {
        "name": name,
        "valor": valor,
        "quantidade": quantidade,
        "valor_num": parse_money(valor),
    }


def _normalize_catalog(stored) -> list[dict]:
    if not isinstance(stored, list):
        return []
    items: list[dict] = []
    seen: set[str] = set()
    for raw in stored:
        item = _normalize_catalog_item(raw)
        if not item:
            continue
        key = item["name"].lower()
        if key in seen:
            continue
        seen.add(key)
        items.append(item)
    return items


def _strip_legacy_defaults(items: list[dict]) -> list[dict]:
    if not items:
        return []
    names = {item["name"].lower() for item in items}
    if names == _LEGACY_DEFAULT_SERVICE_NAMES:
        return []
    return items


def _persistable(items: list[dict]) -> list[dict]:
    return [
        {
            "name": item["name"],
            "valor": item.get("valor") or "",
            "quantidade": int(item.get("quantidade") or 1),
        }
        for item in items
    ]


def list_commercial_service_catalog(*, force_refresh: bool = False) -> list[dict]:
    data = load_app_settings(force_refresh=force_refresh)
    stored = data.get("commercial_services")
    items = _strip_legacy_defaults(_normalize_catalog(stored))
    if items != _normalize_catalog(stored):
        save_app_settings({"commercial_services": _persistable(items)})
    return items


def list_commercial_services(*, force_refresh: bool = False) -> list[str]:
    return [item["name"] for item in list_commercial_service_catalog(force_refresh=force_refresh)]


def get_commercial_service_options() -> list[str]:
    return list_commercial_services()


def get_commercial_service_catalog() -> list[dict]:
    return list_commercial_service_catalog()


def catalog_lookup(name: str) -> dict | None:
    key = _normalize_service_name(name).lower()
    if not key:
        return None
    for item in list_commercial_service_catalog():
        if item["name"].lower() == key:
            return item
    return None


def build_commercial_services_rows() -> list[dict]:
    rows = []
    for item in list_commercial_service_catalog():
        rows.append(
            {
                "name": item["name"],
                "valor": item.get("valor") or "—",
                "quantidade": item.get("quantidade") or 1,
                "status_label": "Ativo",
                "status_class": "active",
            }
        )
    return rows


def add_commercial_service(name: str, valor: str = "", quantidade: str = "1") -> None:
    clean = _normalize_service_name(name)
    if not clean:
        raise ValueError("Informe o nome do serviço.")
    if len(clean) < 2:
        raise ValueError("O nome do serviço deve ter pelo menos 2 caracteres.")

    items = list_commercial_service_catalog(force_refresh=True)
    if any(existing["name"].lower() == clean.lower() for existing in items):
        raise ValueError("Este serviço já está cadastrado.")

    items.append(
        {
            "name": clean,
            "valor": normalize_text(valor),
            "quantidade": _normalize_quantidade(quantidade),
        }
    )
    save_app_settings({"commercial_services": _persistable(items)})


def remove_commercial_service(name: str) -> None:
    clean = _normalize_service_name(name)
    items = list_commercial_service_catalog(force_refresh=True)
    filtered = [item for item in items if item["name"].lower() != clean.lower()]
    if len(filtered) == len(items):
        raise ValueError("Serviço não encontrado.")
    save_app_settings({"commercial_services": _persistable(filtered)})
