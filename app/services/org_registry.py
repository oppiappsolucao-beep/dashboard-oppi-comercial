"""Cadastro interno: setores, funcionários e representantes nacionais."""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import bcrypt

from app.services.registry_store import _lock, connect as _connect, init_store as init_crm_local_db
from app.services.legacy_core import normalize_text

ACCESS_OPTIONS = [
    ("empresas", "Empresas"),
    ("novo_cadastro", "Cadastro"),
    ("ordens", "Ordens de serviço"),
    ("kanban", "Kanban"),
    ("atendimentos", "Atendimentos"),
    ("financeiro", "Financeiro"),
    ("propostas", "Propostas"),
    ("gestao", "Gestão"),
    ("cadastro", "Sistema"),
]

ACCESS_KEYS = {key for key, _label in ACCESS_OPTIONS}
PERSON_KINDS = {"funcionario", "representante"}
BRAZIL_UFS = [
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG",
    "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO",
]
DEFAULT_SECTORS = ["Comercial", "Suporte", "Financeiro", "Representação"]


def _now() -> str:
    return datetime.now(ZoneInfo("America/Sao_Paulo")).replace(tzinfo=None).isoformat(timespec="seconds")


def _loads(raw: str) -> list[str]:
    try:
        parsed = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [key for key in (normalize_text(item) for item in parsed) if key in ACCESS_KEYS]


def _sector_view(row, people_count: int = 0) -> dict:
    accesses = _loads(row["accesses_json"])
    labels = [label for key, label in ACCESS_OPTIONS if key in accesses]
    return {
        "id": row["id"],
        "name": row["name"],
        "accesses": accesses,
        "accesses_csv": ",".join(accesses),
        "access_labels": labels,
        "people_count": people_count,
    }


def _ensure_seed(conn) -> None:
    count = conn.execute("SELECT COUNT(*) AS total FROM org_sectors").fetchone()["total"]
    if count:
        return
    stamp = _now()
    for name in DEFAULT_SECTORS:
        conn.execute(
            """
            INSERT INTO org_sectors (id, name, accesses_json, active, created_at, updated_at)
            VALUES (?, ?, '[]', 1, ?, ?)
            """,
            (f"sec_{uuid.uuid4().hex[:12]}", name, stamp, stamp),
        )


def list_sectors() -> list[dict]:
    init_crm_local_db()
    with _lock, _connect() as conn:
        _ensure_seed(conn)
        rows = conn.execute(
            "SELECT * FROM org_sectors WHERE active = 1 ORDER BY name COLLATE NOCASE"
        ).fetchall()
        counts = {
            row["sector_id"]: row["total"]
            for row in conn.execute(
                """
                SELECT sector_id, COUNT(*) AS total
                FROM org_people
                WHERE active = 1 AND sector_id != ''
                GROUP BY sector_id
                """
            ).fetchall()
        }
    return [_sector_view(row, int(counts.get(row["id"], 0))) for row in rows]


def list_people(kind: str | None = None) -> list[dict]:
    init_crm_local_db()
    with _lock, _connect() as conn:
        _ensure_seed(conn)
        query = """
            SELECT people.*, sectors.name AS sector_name
            FROM org_people AS people
            LEFT JOIN org_sectors AS sectors ON sectors.id = people.sector_id
            WHERE people.active = 1
        """
        params: tuple = ()
        if kind:
            query += " AND people.kind = ?"
            params = (kind,)
        query += " ORDER BY people.name COLLATE NOCASE"
        rows = conn.execute(query, params).fetchall()
    people = []
    for row in rows:
        people.append(
            {
                "id": row["id"],
                "kind": row["kind"],
                "name": row["name"],
                "email": row["email"] or "—",
                "phone": row["phone"] or "—",
                "sector_id": row["sector_id"],
                "sector_name": row["sector_name"] or "—",
                "region": row["region"] or "—",
                "username": (row["username"] if "username" in row.keys() else "") or "—",
                "state_name": (row["state_name"] if "state_name" in row.keys() else "") or "—",
                "city": (row["city"] if "city" in row.keys() else "") or "—",
            }
        )
    return people


def save_sector(*, sector_id: str, name: str, accesses: list[str]) -> dict:
    clean_name = normalize_text(name)
    if len(clean_name) < 2:
        raise ValueError("Informe o nome do setor.")
    if len(clean_name) > 80:
        raise ValueError("O nome do setor pode ter no máximo 80 caracteres.")
    allowed = [key for key in accesses if key in ACCESS_KEYS]
    init_crm_local_db()
    stamp = _now()
    with _lock, _connect() as conn:
        _ensure_seed(conn)
        existing = conn.execute(
            "SELECT id FROM org_sectors WHERE lower(name) = lower(?) AND active = 1",
            (clean_name,),
        ).fetchone()
        if sector_id == "novo":
            if existing:
                raise ValueError("Já existe um setor com esse nome.")
            sector_id = f"sec_{uuid.uuid4().hex[:12]}"
            conn.execute(
                """
                INSERT INTO org_sectors (id, name, accesses_json, active, created_at, updated_at)
                VALUES (?, ?, ?, 1, ?, ?)
                """,
                (sector_id, clean_name, json.dumps(allowed), stamp, stamp),
            )
        else:
            current = conn.execute(
                "SELECT id FROM org_sectors WHERE id = ? AND active = 1",
                (sector_id,),
            ).fetchone()
            if current is None:
                raise ValueError("Setor não encontrado.")
            if existing and existing["id"] != sector_id:
                raise ValueError("Já existe um setor com esse nome.")
            conn.execute(
                """
                UPDATE org_sectors
                SET name = ?, accesses_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (clean_name, json.dumps(allowed), stamp, sector_id),
            )
    saved = next((item for item in list_sectors() if item["id"] == sector_id), None)
    if saved is None:
        raise ValueError("Não consegui recarregar o setor.")
    return saved


def remove_sector(sector_id: str) -> None:
    init_crm_local_db()
    with _lock, _connect() as conn:
        conn.execute("UPDATE org_sectors SET active = 0, updated_at = ? WHERE id = ?", (_now(), sector_id))
        conn.execute(
            "UPDATE org_people SET sector_id = '', updated_at = ? WHERE sector_id = ?",
            (_now(), sector_id),
        )


def _normalize_username(value: str) -> str:
    username = normalize_text(value).lower()
    if not re.match(r"^[a-z0-9._-]{3,40}$", username):
        raise ValueError("O login deve ter 3 a 40 caracteres (letras, números, ., - ou _).")
    return username


def _hash_password(password: str) -> str:
    clean = str(password or "")
    if len(clean.strip()) < 6:
        raise ValueError("A senha deve ter pelo menos 6 caracteres.")
    return bcrypt.hashpw(clean.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def save_person(
    *,
    kind: str,
    name: str,
    sector_id: str = "",
    email: str = "",
    phone: str = "",
    region: str = "",
    username: str = "",
    password: str = "",
    state_name: str = "",
    city: str = "",
) -> dict:
    if kind not in PERSON_KINDS:
        raise ValueError("Tipo de cadastro inválido.")
    clean_name = normalize_text(name)
    if len(clean_name) < 2:
        raise ValueError("Informe o nome.")
    clean_username = _normalize_username(username)
    password_hash = _hash_password(password)
    clean_region = normalize_text(region).upper()
    clean_state = normalize_text(state_name)
    clean_city = normalize_text(city)
    if kind == "representante":
        if clean_region and clean_region not in BRAZIL_UFS:
            raise ValueError("Selecione a UF do representante.")
        if len(clean_state) < 2:
            raise ValueError("Informe o estado em que o representante reside.")
        if len(clean_city) < 2:
            raise ValueError("Informe a cidade em que o representante reside.")
    init_crm_local_db()
    stamp = _now()
    person_id = f"pes_{uuid.uuid4().hex[:12]}"
    with _lock, _connect() as conn:
        _ensure_seed(conn)
        taken = conn.execute(
            "SELECT id FROM org_people WHERE lower(username) = ? AND active = 1",
            (clean_username,),
        ).fetchone()
        if taken:
            raise ValueError("Este login já está em uso.")
        sector_name = ""
        stored_sector_id = ""
        if kind == "funcionario":
            sector = conn.execute(
                "SELECT id, name FROM org_sectors WHERE id = ? AND active = 1",
                (normalize_text(sector_id),),
            ).fetchone()
            if sector is None:
                raise ValueError("Selecione o setor.")
            stored_sector_id = sector["id"]
            sector_name = sector["name"]
        conn.execute(
            """
            INSERT INTO org_people (
                id, kind, name, email, phone, sector_id, region, username, password_hash,
                state_name, city, active, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                person_id,
                kind,
                clean_name,
                normalize_text(email),
                normalize_text(phone),
                stored_sector_id,
                clean_region,
                clean_username,
                password_hash,
                clean_state,
                clean_city,
                stamp,
                stamp,
            ),
        )
    return {"id": person_id, "name": clean_name, "sector_name": sector_name, "username": clean_username}


def remove_person(person_id: str) -> None:
    init_crm_local_db()
    with _lock, _connect() as conn:
        conn.execute(
            "UPDATE org_people SET active = 0, updated_at = ? WHERE id = ?",
            (_now(), person_id),
        )


def authenticate_employee(username: str, password: str) -> dict | None:
    """Login de funcionário. Representante fica cadastrado, mas ainda não entra no sistema."""
    clean_username = normalize_text(username).lower()
    clean_password = str(password or "")
    if not clean_username or not clean_password:
        return None
    init_crm_local_db()
    with _lock, _connect() as conn:
        row = conn.execute(
            """
            SELECT people.*, sectors.name AS sector_name, sectors.accesses_json
            FROM org_people AS people
            LEFT JOIN org_sectors AS sectors ON sectors.id = people.sector_id AND sectors.active = 1
            WHERE lower(people.username) = ? AND people.active = 1 AND people.kind = 'funcionario'
            """,
            (clean_username,),
        ).fetchone()
    if row is None:
        return None
    stored = row["password_hash"] or ""
    try:
        valid = bool(stored) and bcrypt.checkpw(clean_password.encode("utf-8"), stored.encode("utf-8"))
    except ValueError:
        valid = False
    if not valid:
        return None
    accesses = _loads(row["accesses_json"] or "[]")
    return {
        "id": row["id"],
        "name": row["name"],
        "username": row["username"],
        "sector_id": row["sector_id"] or "",
        "sector_name": row["sector_name"] or "",
        "accesses": accesses,
    }


def list_sector_queues(sector_id: str) -> list[dict]:
    init_crm_local_db()
    with _lock, _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, name, position
            FROM org_queues
            WHERE sector_id = ? AND active = 1
            ORDER BY position, name COLLATE NOCASE
            """,
            (normalize_text(sector_id),),
        ).fetchall()
    return [{"id": row["id"], "name": row["name"], "position": row["position"]} for row in rows]


def add_sector_queue(sector_id: str, name: str) -> dict:
    clean_name = normalize_text(name)
    if len(clean_name) < 2:
        raise ValueError("Informe o nome da fila.")
    if len(clean_name) > 40:
        raise ValueError("O nome da fila pode ter no máximo 40 caracteres.")
    if clean_name.lower() == "análise" or clean_name.lower() == "analise":
        raise ValueError("A primeira coluna já é Análise.")
    init_crm_local_db()
    stamp = _now()
    queue_id = f"fila_{uuid.uuid4().hex[:12]}"
    with _lock, _connect() as conn:
        sector = conn.execute(
            "SELECT id FROM org_sectors WHERE id = ? AND active = 1",
            (normalize_text(sector_id),),
        ).fetchone()
        if sector is None:
            raise ValueError("Setor não encontrado.")
        last = conn.execute(
            "SELECT COALESCE(MAX(position), 0) AS last_pos FROM org_queues WHERE sector_id = ? AND active = 1",
            (sector["id"],),
        ).fetchone()
        position = int(last["last_pos"] or 0) + 1
        conn.execute(
            """
            INSERT INTO org_queues (id, sector_id, name, position, active, created_at)
            VALUES (?, ?, ?, ?, 1, ?)
            """,
            (queue_id, sector["id"], clean_name, position, stamp),
        )
    return {"id": queue_id, "name": clean_name, "position": position}


def validate_service_assignment(sector_name: str, responsible_name: str) -> tuple[str, str]:
    sector = normalize_text(sector_name)
    responsible = normalize_text(responsible_name)
    if not sector:
        raise ValueError("Selecione o setor da ordem de serviço.")
    if not responsible:
        raise ValueError("Selecione o responsável da ordem de serviço.")
    people = [
        person
        for person in list_people()
        if person["sector_name"].lower() == sector.lower() and person["name"].lower() == responsible.lower()
    ]
    if not people:
        raise ValueError("O responsável precisa estar cadastrado nesse setor.")
    return people[0]["sector_name"], people[0]["name"]
