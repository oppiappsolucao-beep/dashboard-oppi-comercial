"""Serviços comerciais cadastrados em Configurações — usados em propostas e cadastros."""
from __future__ import annotations

from app.services.app_settings import load_app_settings, save_app_settings
from app.services.legacy_core import normalize_text

# Exemplos antigos do código — não devem reaparecer como cadastro automático.
_LEGACY_DEFAULT_SERVICE_NAMES = frozenset({"oppi vision", "oppi flow", "oppi track"})


def _normalize_service_name(value: str) -> str:
    return normalize_text(value)


def _normalize_service_list(stored) -> list[str]:
    if not isinstance(stored, list):
        return []

    services: list[str] = []
    seen: set[str] = set()
    for item in stored:
        name = _normalize_service_name(str(item))
        key = name.lower()
        if not name or key in seen:
            continue
        seen.add(key)
        services.append(name)
    return services


def _strip_legacy_defaults(services: list[str]) -> list[str]:
    if not services:
        return []
    if {name.lower() for name in services} == _LEGACY_DEFAULT_SERVICE_NAMES:
        return []
    return services


def list_commercial_services(*, force_refresh: bool = False) -> list[str]:
    data = load_app_settings(force_refresh=force_refresh)
    stored = data.get("commercial_services")
    services = _strip_legacy_defaults(_normalize_service_list(stored))
    if services != _normalize_service_list(stored):
        save_app_settings({"commercial_services": services})
    return services


def get_commercial_service_options() -> list[str]:
    return list_commercial_services()


def build_commercial_services_rows() -> list[dict]:
    return [
        {
            "name": name,
            "status_label": "Ativo",
            "status_class": "active",
        }
        for name in list_commercial_services()
    ]


def add_commercial_service(name: str) -> None:
    clean = _normalize_service_name(name)
    if not clean:
        raise ValueError("Informe o nome do serviço.")
    if len(clean) < 2:
        raise ValueError("O nome do serviço deve ter pelo menos 2 caracteres.")

    services = list_commercial_services(force_refresh=True)
    if any(existing.lower() == clean.lower() for existing in services):
        raise ValueError("Este serviço já está cadastrado.")

    services.append(clean)
    save_app_settings({"commercial_services": services})


def remove_commercial_service(name: str) -> None:
    clean = _normalize_service_name(name)
    services = list_commercial_services(force_refresh=True)
    filtered = [item for item in services if item.lower() != clean.lower()]
    if len(filtered) == len(services):
        raise ValueError("Serviço não encontrado.")
    save_app_settings({"commercial_services": filtered})
