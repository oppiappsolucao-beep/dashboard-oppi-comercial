"""Serviços comerciais cadastrados em Configurações — usados em propostas e cadastros."""
from __future__ import annotations

from app.services.app_settings import load_app_settings, save_app_settings
from app.services.legacy_core import normalize_text

DEFAULT_COMMERCIAL_SERVICES = ["Oppi Vision", "Oppi Flow", "Oppi Track"]


def _normalize_service_name(value: str) -> str:
    return normalize_text(value)


def list_commercial_services() -> list[str]:
    data = load_app_settings()
    stored = data.get("commercial_services")
    if not isinstance(stored, list):
        return list(DEFAULT_COMMERCIAL_SERVICES)

    services: list[str] = []
    seen: set[str] = set()
    for item in stored:
        name = _normalize_service_name(str(item))
        key = name.lower()
        if not name or key in seen:
            continue
        seen.add(key)
        services.append(name)

    return services or list(DEFAULT_COMMERCIAL_SERVICES)


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

    services = list_commercial_services()
    if any(existing.lower() == clean.lower() for existing in services):
        raise ValueError("Este serviço já está cadastrado.")

    services.append(clean)
    save_app_settings({"commercial_services": services})


def remove_commercial_service(name: str) -> None:
    clean = _normalize_service_name(name)
    services = list_commercial_services()
    filtered = [item for item in services if item.lower() != clean.lower()]
    if len(filtered) == len(services):
        raise ValueError("Serviço não encontrado.")
    if not filtered:
        raise ValueError("Cadastre pelo menos um serviço comercial.")
    save_app_settings({"commercial_services": filtered})
