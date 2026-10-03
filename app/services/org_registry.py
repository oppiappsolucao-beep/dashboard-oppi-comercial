"""Cadastro interno: setores, funcionários e representantes nacionais."""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from app.services.crm_local_db import _connect, _lock, init_crm_local_db
from app.services.legacy_core import normalize_text

ACCESS_OPTIONS = [
    ("empresas", "Empresas"),
    ("ordens", "Ordens de serviço"),
    ("kanban", "Kanban"),
    ("atendimentos", "Atendimentos"),
    ("financeiro", "Financeiro"),
    ("propostas", "Propostas"),
    ("gestao", "Gestão"),
    ("cadastro", "Cadastro"),
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


def save_person(
    *,
    kind: str,
    name: str,
    sector_id: str,
    email: str = "",
    phone: str = "",
    region: str = "",
) -> dict:
    if kind not in PERSON_KINDS:
        raise ValueError("Tipo de cadastro inválido.")
    clean_name = normalize_text(name)
    if len(clean_name) < 2:
        raise ValueError("Informe o nome.")
    clean_region = normalize_text(region).upper()
    if kind == "representante" and clean_region and clean_region not in BRAZIL_UFS:
        raise ValueError("Selecione a UF do representante.")
    init_crm_local_db()
    stamp = _now()
    person_id = f"pes_{uuid.uuid4().hex[:12]}"
    with _lock, _connect() as conn:
        _ensure_seed(conn)
        sector = conn.execute(
            "SELECT id, name FROM org_sectors WHERE id = ? AND active = 1",
            (normalize_text(sector_id),),
        ).fetchone()
        if sector is None:
            raise ValueError("Selecione o setor.")
        conn.execute(
            """
            INSERT INTO org_people (
                id, kind, name, email, phone, sector_id, region, active, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                person_id,
                kind,
                clean_name,
                normalize_text(email),
                normalize_text(phone),
                sector["id"],
                clean_region,
                stamp,
                stamp,
            ),
        )
    return {"id": person_id, "name": clean_name, "sector_name": sector["name"]}


def remove_person(person_id: str) -> None:
    init_crm_local_db()
    with _lock, _connect() as conn:
        conn.execute(
            "UPDATE org_people SET active = 0, updated_at = ? WHERE id = ?",
            (_now(), person_id),
        )


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
