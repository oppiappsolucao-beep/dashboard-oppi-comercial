"""Usuários da conta — cadastro, edição e autenticação."""
from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path

from passlib.context import CryptContext

from app.config import settings
from app.services.legacy_core import normalize_text
from app.services.sheet_crm_storage import CRM_STORAGE_TABS, get_worksheet, header_indexes
from app.services.storage_paths import get_storage_dir

ROLE_CLASS = {
    "Administrador": "admin",
    "Gerente": "manager",
    "Vendedor": "seller",
    "Analista": "analyst",
}

VALID_ROLES = set(ROLE_CLASS.keys())
USERS_WORKSHEET = "Usuarios"
USERS_HEADERS = CRM_STORAGE_TABS[USERS_WORKSHEET]
_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
_lock = threading.Lock()
_cache: list[dict] | None = None


def _users_file_path() -> Path:
    return get_storage_dir() / "account_users.json"


def _normalize_email(value: str) -> str:
    email = normalize_text(value).lower()
    if not email:
        return ""
    if "@" not in email or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        raise ValueError("Informe um e-mail válido.")
    return email


def _now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _normalize_username(value: str) -> str:
    username = normalize_text(value).lower()
    if not username:
        raise ValueError("Informe o usuário de login.")
    if not re.match(r"^[a-z0-9._-]{3,40}$", username):
        raise ValueError("Usuário de login deve ter 3 a 40 caracteres (letras, números, ., - ou _).")
    return username


def _normalize_name(value: str) -> str:
    name = normalize_text(value)
    if len(name) < 2:
        raise ValueError("Informe o nome do usuário.")
    return name


def _normalize_role(value: str) -> str:
    role = normalize_text(value)
    if role not in VALID_ROLES:
        raise ValueError("Selecione um perfil válido.")
    return role


def _hash_password(password: str) -> str:
    clean = password.strip()
    if len(clean) < 6:
        raise ValueError("A senha deve ter pelo menos 6 caracteres.")
    return _pwd_context.hash(clean)


def _verify_password(password: str, password_hash: str) -> bool:
    try:
        return _pwd_context.verify(password, password_hash)
    except Exception:
        return False


def _serialize_user(raw: dict) -> dict:
    role = _normalize_role(raw.get("role", "Vendedor"))
    active = bool(raw.get("active", True))
    last_access = normalize_text(raw.get("last_access", ""))
    return {
        "id": normalize_text(raw.get("id", "")) or str(uuid.uuid4()),
        "name": _normalize_name(raw.get("name", "")),
        "email": _normalize_email(raw.get("email", "")),
        "username": _normalize_username(raw.get("username", "")),
        "password_hash": normalize_text(raw.get("password_hash", "")),
        "role": role,
        "role_class": ROLE_CLASS[role],
        "active": active,
        "status_label": "Ativo" if active else "Inativo",
        "status_class": "active" if active else "blocked",
        "last_access": last_access,
        "created_at": normalize_text(raw.get("created_at", "")) or _now_iso(),
        "updated_at": normalize_text(raw.get("updated_at", "")) or _now_iso(),
    }


def _load_from_file() -> list[dict]:
    path = _users_file_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, list):
        return []

    users: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            users.append(_serialize_user(item))
        except ValueError:
            continue
    return users


def _save_to_file(users: list[dict]) -> None:
    path = _users_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = []
    for user in users:
        payload.append({
            "id": user["id"],
            "name": user["name"],
            "email": user["email"],
            "username": user["username"],
            "password_hash": user["password_hash"],
            "role": user["role"],
            "active": user["active"],
            "last_access": user.get("last_access", ""),
            "created_at": user.get("created_at", _now_iso()),
            "updated_at": user.get("updated_at", _now_iso()),
        })
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _user_row_from_sheet(row: list[str], indexes: dict[str, int]) -> dict | None:
    if len(row) <= max(indexes.values()):
        return None
    try:
        return _serialize_user({
            "id": row[indexes["Id"]],
            "name": row[indexes["Nome"]],
            "email": row[indexes["Email"]],
            "username": row[indexes["Usuario"]],
            "password_hash": row[indexes["SenhaHash"]],
            "role": row[indexes["Perfil"]],
            "active": normalize_text(row[indexes["Ativo"]]).lower() in {"1", "true", "sim", "ativo", "yes"},
            "last_access": row[indexes["UltimoAcesso"]],
            "created_at": row[indexes["CriadoEm"]],
            "updated_at": row[indexes["AtualizadoEm"]],
        })
    except ValueError:
        return None


def _load_from_sheet() -> list[dict] | None:
    if not settings.sheets_configured:
        return None
    worksheet = get_worksheet(USERS_WORKSHEET)
    if worksheet is None:
        return None
    try:
        rows = worksheet.get_all_values()
    except Exception:
        return None
    if len(rows) < 2:
        return []

    indexes = header_indexes(rows[0], USERS_HEADERS)
    if indexes is None:
        return None

    users: list[dict] = []
    for row in rows[1:]:
        user = _user_row_from_sheet(row, indexes)
        if user:
            users.append(user)
    return users


def _save_to_sheet(users: list[dict]) -> bool:
    if not settings.sheets_configured:
        return False
    worksheet = get_worksheet(USERS_WORKSHEET)
    if worksheet is None:
        return False
    try:
        rows = [USERS_HEADERS]
        for user in sorted(users, key=lambda item: item.get("username", "").lower()):
            rows.append([
                user["id"],
                user["name"],
                user["email"],
                user["username"],
                user["password_hash"],
                user["role"],
                "1" if user["active"] else "0",
                user.get("last_access", ""),
                user.get("created_at", ""),
                user.get("updated_at", ""),
            ])
        worksheet.clear()
        worksheet.update(rows, value_input_option="USER_ENTERED")
        return True
    except Exception:
        return False


def _persist_users(users: list[dict]) -> None:
    _save_to_file(users)
    _save_to_sheet(users)


def load_account_users(force_refresh: bool = False) -> list[dict]:
    global _cache
    with _lock:
        if not force_refresh and _cache is not None:
            return [dict(user) for user in _cache]

        file_users = _load_from_file()
        sheet_users = _load_from_sheet()
        merged_by_id: dict[str, dict] = {user["id"]: user for user in file_users}
        if sheet_users is not None:
            for user in sheet_users:
                merged_by_id[user["id"]] = user
            merged = list(merged_by_id.values())
            if merged != file_users:
                _save_to_file(merged)
        else:
            merged = file_users

        _cache = merged
        return [dict(user) for user in merged]


def invalidate_account_users_cache() -> None:
    global _cache
    with _lock:
        _cache = None


def get_account_user_by_username(username: str) -> dict | None:
    target = normalize_text(username).lower()
    if not target:
        return None
    for user in load_account_users():
        if user["username"].lower() == target and user["active"]:
            return dict(user)
    return None


def get_account_user_by_id(user_id: str) -> dict | None:
    target = normalize_text(user_id)
    if not target:
        return None
    for user in load_account_users():
        if user["id"] == target:
            return dict(user)
    return None


def verify_account_user_credentials(username: str, password: str) -> dict | None:
    user = get_account_user_by_username(username)
    if not user:
        return None
    if not _verify_password(password, user.get("password_hash", "")):
        return None
    return user


def touch_account_user_last_access(user_id: str) -> None:
    users = load_account_users()
    updated = False
    for user in users:
        if user["id"] == user_id:
            user["last_access"] = datetime.now().strftime("%d/%m/%Y %H:%M")
            user["updated_at"] = _now_iso()
            updated = True
            break
    if updated:
        _persist_users(users)
        with _lock:
            global _cache
            _cache = users


def create_account_user(
    *,
    name: str,
    email: str,
    username: str,
    password: str,
    role: str,
    active: bool = True,
) -> dict:
    users = load_account_users()
    clean_name = _normalize_name(name)
    clean_email = _normalize_email(email)
    clean_username = _normalize_username(username)
    clean_role = _normalize_role(role)

    if clean_email and any(user["email"] == clean_email for user in users):
        raise ValueError("Já existe um usuário com este e-mail.")
    if any(user["username"] == clean_username for user in users):
        raise ValueError("Já existe um usuário com este login.")

    user = _serialize_user({
        "id": str(uuid.uuid4()),
        "name": clean_name,
        "email": clean_email,
        "username": clean_username,
        "password_hash": _hash_password(password),
        "role": clean_role,
        "active": active,
        "last_access": "",
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    })
    users.append(user)
    _persist_users(users)
    with _lock:
        global _cache
        _cache = users
    return dict(user)


def update_account_user(
    user_id: str,
    *,
    name: str,
    email: str,
    username: str,
    role: str,
    active: bool,
    password: str | None = None,
) -> dict:
    users = load_account_users()
    clean_name = _normalize_name(name)
    clean_email = _normalize_email(email)
    clean_username = _normalize_username(username)
    clean_role = _normalize_role(role)

    index = next((idx for idx, user in enumerate(users) if user["id"] == user_id), None)
    if index is None:
        raise ValueError("Usuário não encontrado.")

    if clean_email and any(user["email"] == clean_email and user["id"] != user_id for user in users):
        raise ValueError("Já existe um usuário com este e-mail.")
    if any(user["username"] == clean_username and user["id"] != user_id for user in users):
        raise ValueError("Já existe um usuário com este login.")

    current = users[index]
    current.update({
        "name": clean_name,
        "email": clean_email,
        "username": clean_username,
        "role": clean_role,
        "role_class": ROLE_CLASS[clean_role],
        "active": bool(active),
        "status_label": "Ativo" if active else "Inativo",
        "status_class": "active" if active else "blocked",
        "updated_at": _now_iso(),
    })
    if password and password.strip():
        current["password_hash"] = _hash_password(password)

    users[index] = current
    _persist_users(users)
    with _lock:
        global _cache
        _cache = users
    return dict(current)


def delete_account_user(user_id: str) -> None:
    users = load_account_users()
    filtered = [user for user in users if user["id"] != user_id]
    if len(filtered) == len(users):
        raise ValueError("Usuário não encontrado.")
    _persist_users(filtered)
    with _lock:
        global _cache
        _cache = filtered
