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
    ("agenda", "Agenda"),
    ("kanban", "Kanban"),
    ("atendimentos", "Atendimentos"),
    ("financeiro", "Financeiro"),
    ("propostas", "Propostas"),
    ("gestao", "Gestão"),
    ("cadastro", "Sistema"),
]

ACCESS_KEYS = {key for key, _label in ACCESS_OPTIONS}
PERSON_KINDS = {"funcionario", "representante", "treinador"}
BRAZIL_UFS = [
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG",
    "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO",
]
DEFAULT_SECTORS = ["Comercial", "Suporte", "Financeiro", "Representação"]
COMMERCIAL_CADASTRO_ACCESS = ("novo_cadastro", "empresas", "propostas")
TRAINING_ACCESS = ("agenda", "kanban", "empresas")
SCHEDULE_DAYS = (
    ("seg", "Seg"),
    ("ter", "Ter"),
    ("qua", "Qua"),
    ("qui", "Qui"),
    ("sex", "Sex"),
    ("sab", "Sáb"),
    ("dom", "Dom"),
)
SCHEDULE_DAY_KEYS = [key for key, _label in SCHEDULE_DAYS]
TRAINING_SLOT_STARTS = tuple(f"{hour:02d}:00" for hour in range(8, 21))


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


def _ensure_training_sector(conn) -> None:
    row = conn.execute(
        "SELECT id, accesses_json FROM org_sectors WHERE lower(name) = ? AND active = 1",
        ("treinamento",),
    ).fetchone()
    stamp = _now()
    if row is None:
        conn.execute(
            """
            INSERT INTO org_sectors (id, name, accesses_json, active, created_at, updated_at)
            VALUES (?, ?, ?, 1, ?, ?)
            """,
            (
                f"sec_{uuid.uuid4().hex[:12]}",
                "Treinamento",
                json.dumps(list(TRAINING_ACCESS)),
                stamp,
                stamp,
            ),
        )
        return
    current = _loads(row["accesses_json"])
    merged = list(dict.fromkeys([*current, *TRAINING_ACCESS]))
    if merged == current:
        return
    conn.execute(
        "UPDATE org_sectors SET accesses_json = ?, updated_at = ? WHERE id = ?",
        (json.dumps(merged), stamp, row["id"]),
    )


def _ensure_commercial_cadastro(conn) -> None:
    """Comercial cadastra cliente como o acesso administrativo."""
    rows = conn.execute(
        "SELECT id, name, accesses_json FROM org_sectors WHERE active = 1"
    ).fetchall()
    stamp = _now()
    for row in rows:
        if "comercial" not in (row["name"] or "").lower():
            continue
        current = _loads(row["accesses_json"])
        merged = list(dict.fromkeys([*current, *COMMERCIAL_CADASTRO_ACCESS]))
        if merged == current:
            continue
        conn.execute(
            "UPDATE org_sectors SET accesses_json = ?, updated_at = ? WHERE id = ?",
            (json.dumps(merged), stamp, row["id"]),
        )


def list_sectors() -> list[dict]:
    init_crm_local_db()
    with _lock, _connect() as conn:
        _ensure_seed(conn)
        _ensure_training_sector(conn)
        _ensure_commercial_cadastro(conn)
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


def empty_schedule() -> dict:
    return {"days": [], "slots": []}


def slot_label(start: str) -> str:
    text = normalize_text(start)
    if not re.match(r"^\d{2}:\d{2}$", text):
        return text
    end_hour = int(text[:2]) + 1
    return f"{text} – {end_hour:02d}:{text[3:]}"


def training_slot_choices() -> list[dict]:
    return [{"value": start, "label": slot_label(start)} for start in TRAINING_SLOT_STARTS]


def _schedule_from_raw(raw: str) -> dict:
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    days = []
    for day in data.get("days") or []:
        key = normalize_text(day).lower()
        if key in SCHEDULE_DAY_KEYS and key not in days:
            days.append(key)
    days.sort(key=SCHEDULE_DAY_KEYS.index)
    slots = []
    for item in data.get("slots") or []:
        key = normalize_text(item)[:5]
        if key in TRAINING_SLOT_STARTS and key not in slots:
            slots.append(key)
    if not slots:
        start = normalize_text(data.get("start"))[:5]
        end = normalize_text(data.get("end"))[:5]
        if re.match(r"^\d{2}:\d{2}$", start) and re.match(r"^\d{2}:\d{2}$", end) and start < end:
            slots = [key for key in TRAINING_SLOT_STARTS if start <= key < end]
    slots.sort()
    return {"days": days, "slots": slots}


def schedule_label(schedule: dict) -> str:
    days = schedule.get("days") or []
    slots = schedule.get("slots") or []
    if not days or not slots:
        return "Agenda não definida"
    names = [label for key, label in SCHEDULE_DAYS if key in days]
    periods = ", ".join(slot_label(slot) for slot in slots)
    return f"{', '.join(names)} · {periods}"


def parse_trainer_schedule(days, slots) -> str:
    chosen_days = []
    for day in days or []:
        key = normalize_text(day).lower()
        if key in SCHEDULE_DAY_KEYS and key not in chosen_days:
            chosen_days.append(key)
    chosen_days.sort(key=SCHEDULE_DAY_KEYS.index)
    chosen_slots = []
    for slot in slots or []:
        key = normalize_text(slot)[:5]
        if key in TRAINING_SLOT_STARTS and key not in chosen_slots:
            chosen_slots.append(key)
    chosen_slots.sort()
    if not chosen_days:
        raise ValueError("Marque os dias em que o treinador atende.")
    if not chosen_slots:
        raise ValueError("Marque os horários de 1 hora em que o treinador atende.")
    return json.dumps({"days": chosen_days, "slots": chosen_slots}, ensure_ascii=False)


def ensure_training_slot(trainer: dict, day: str, hour: str) -> None:
    """O horário precisa ser um período de 1 hora da agenda do treinador."""
    schedule = trainer.get("schedule") or empty_schedule()
    name = trainer.get("name") or "O treinador"
    slots = schedule.get("slots") or []
    days = schedule.get("days") or []
    if not days or not slots:
        raise ValueError(f"Cadastre os horários de {name} em Sistema.")
    hour_text = normalize_text(hour)[:5]
    try:
        weekday = datetime.fromisoformat(normalize_text(day)).weekday()
    except ValueError as error:
        raise ValueError("Informe a data do treinamento.") from error
    if SCHEDULE_DAY_KEYS[weekday] not in days:
        raise ValueError(f"{name} não atende nesse dia da semana.")
    if hour_text not in slots:
        periods = ", ".join(slot_label(slot) for slot in slots)
        raise ValueError(f"{name} atende nestes horários: {periods}.")


def available_training_slots(trainer: dict, day: str) -> tuple[list[dict], str]:
    """Horários de 1 hora livres para o treinador na data escolhida."""
    schedule = trainer.get("schedule") or empty_schedule()
    name = trainer.get("name") or "O treinador"
    slots = schedule.get("slots") or []
    days = schedule.get("days") or []
    if not days or not slots:
        return [], "Este treinador ainda não tem horários cadastrados."
    try:
        weekday = datetime.fromisoformat(normalize_text(day)).weekday()
    except ValueError:
        return [], "Informe a data do treinamento."
    if SCHEDULE_DAY_KEYS[weekday] not in days:
        return [], f"{name} não atende nesse dia da semana."
    from app.services.service_orders import trainer_busy_hours

    busy = trainer_busy_hours(name, day)
    open_slots = [
        {"value": start, "label": slot_label(start)}
        for start in slots
        if start not in busy
    ]
    if not open_slots:
        return [], "Nenhum horário livre neste dia."
    return open_slots, ""


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
        schedule = _schedule_from_raw(row["schedule_json"] if "schedule_json" in row.keys() else "")
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
                "schedule": schedule,
                "schedule_label": schedule_label(schedule),
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


def get_person(person_id: str) -> dict | None:
    init_crm_local_db()
    with _lock, _connect() as conn:
        row = conn.execute(
            """
            SELECT people.*, sectors.name AS sector_name
            FROM org_people AS people
            LEFT JOIN org_sectors AS sectors ON sectors.id = people.sector_id
            WHERE people.id = ? AND people.active = 1
            """,
            (normalize_text(person_id),),
        ).fetchone()
    if row is None:
        return None

    def raw(key: str) -> str:
        if key not in row.keys():
            return ""
        return normalize_text(row[key])

    schedule = _schedule_from_raw(row["schedule_json"] if "schedule_json" in row.keys() else "")
    return {
        "id": row["id"],
        "kind": row["kind"],
        "name": raw("name"),
        "email": raw("email"),
        "phone": raw("phone"),
        "sector_id": raw("sector_id"),
        "sector_name": normalize_text(row["sector_name"]) if "sector_name" in row.keys() else "",
        "region": raw("region"),
        "username": raw("username"),
        "state_name": raw("state_name"),
        "city": raw("city"),
        "has_password": bool(row["password_hash"] if "password_hash" in row.keys() else ""),
        "schedule": schedule,
        "schedule_label": schedule_label(schedule),
    }


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
    person_id: str = "",
    schedule_json: str = "",
) -> dict:
    if kind not in PERSON_KINDS:
        raise ValueError("Tipo de cadastro inválido.")
    clean_name = normalize_text(name)
    if len(clean_name) < 2:
        raise ValueError("Informe o nome.")
    clean_person_id = normalize_text(person_id)
    password_text = str(password or "").strip()
    clean_username = _normalize_username(username)
    if password_text:
        password_hash = _hash_password(password_text)
    elif clean_person_id:
        password_hash = ""
    else:
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
    updated = False
    with _lock, _connect() as conn:
        _ensure_seed(conn)
        current = None
        if clean_person_id:
            current = conn.execute(
                "SELECT * FROM org_people WHERE id = ? AND active = 1",
                (clean_person_id,),
            ).fetchone()
            if current is None:
                raise ValueError("Cadastro não encontrado.")
            if current["kind"] != kind:
                raise ValueError("Tipo de cadastro inválido.")
            if not password_hash:
                password_hash = current["password_hash"] or ""
        stored_schedule = schedule_json if kind == "treinador" else "{}"
        if kind == "treinador" and not stored_schedule:
            raise ValueError("Informe a agenda do treinador.")
        taken = conn.execute(
            "SELECT id FROM org_people WHERE lower(username) = ? AND active = 1 AND id != ?",
            (clean_username, clean_person_id),
        ).fetchone()
        if taken:
            raise ValueError("Este login já está em uso.")
        sector_name = ""
        stored_sector_id = ""
        if kind in {"funcionario", "treinador"}:
            sector = conn.execute(
                "SELECT id, name FROM org_sectors WHERE id = ? AND active = 1",
                (normalize_text(sector_id),),
            ).fetchone()
            if sector is None:
                raise ValueError("Selecione o setor.")
            stored_sector_id = sector["id"]
            sector_name = sector["name"]
        if current is None:
            clean_person_id = f"pes_{uuid.uuid4().hex[:12]}"
            conn.execute(
                """
                INSERT INTO org_people (
                    id, kind, name, email, phone, sector_id, region, username, password_hash,
                    state_name, city, schedule_json, active, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    clean_person_id,
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
                    stored_schedule,
                    stamp,
                    stamp,
                ),
            )
        else:
            updated = True
            conn.execute(
                """
                UPDATE org_people
                SET name = ?, email = ?, phone = ?, sector_id = ?, region = ?, username = ?,
                    password_hash = ?, state_name = ?, city = ?, schedule_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    clean_name,
                    normalize_text(email),
                    normalize_text(phone),
                    stored_sector_id,
                    clean_region,
                    clean_username,
                    password_hash,
                    clean_state,
                    clean_city,
                    stored_schedule,
                    stamp,
                    clean_person_id,
                ),
            )
    return {
        "id": clean_person_id,
        "name": clean_name,
        "sector_name": sector_name,
        "username": clean_username,
        "updated": updated,
    }


def remove_person(person_id: str) -> None:
    init_crm_local_db()
    with _lock, _connect() as conn:
        conn.execute(
            "UPDATE org_people SET active = 0, updated_at = ? WHERE id = ?",
            (_now(), person_id),
        )


def authenticate_employee(username: str, password: str) -> dict | None:
    """Login de funcionário ou treinador. Representante fica cadastrado, mas ainda não entra no sistema."""
    clean_username = normalize_text(username).lower()
    clean_password = str(password or "")
    if not clean_username or not clean_password:
        return None
    list_sectors()
    init_crm_local_db()
    with _lock, _connect() as conn:
        row = conn.execute(
            """
            SELECT people.*, sectors.name AS sector_name, sectors.accesses_json
            FROM org_people AS people
            LEFT JOIN org_sectors AS sectors ON sectors.id = people.sector_id AND sectors.active = 1
            WHERE lower(people.username) = ? AND people.active = 1 AND people.kind IN ('funcionario', 'treinador')
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
        "kind": row["kind"],
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
    if clean_name.lower() in {"análise", "analise"}:
        raise ValueError("A primeira coluna já é Análise.")
    if clean_name.lower() in {"concluída", "concluida", "finalizada", "finalizado"}:
        raise ValueError("A última coluna já é Concluída.")
    if clean_name.lower() in {"campanha", "campanhas"}:
        raise ValueError("A coluna Campanha já é fixa no comercial.")
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


def move_sector_queue(sector_id: str, queue_id: str, direction: str) -> None:
    step = -1 if normalize_text(direction).lower() in {"esquerda", "left", "-1"} else 1
    init_crm_local_db()
    with _lock, _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, position
            FROM org_queues
            WHERE sector_id = ? AND active = 1
            ORDER BY position, name COLLATE NOCASE
            """,
            (normalize_text(sector_id),),
        ).fetchall()
        ordered = [row["id"] for row in rows]
        if queue_id not in ordered:
            raise ValueError("Coluna não encontrada.")
        index = ordered.index(queue_id)
        target = index + step
        if target < 0 or target >= len(ordered):
            return
        ordered[index], ordered[target] = ordered[target], ordered[index]
        for position, item_id in enumerate(ordered, start=1):
            conn.execute(
                "UPDATE org_queues SET position = ? WHERE id = ?",
                (position, item_id),
            )


def remove_sector_queue(sector_id: str, queue_id: str) -> str:
    init_crm_local_db()
    with _lock, _connect() as conn:
        sector = conn.execute(
            "SELECT id, name FROM org_sectors WHERE id = ? AND active = 1",
            (normalize_text(sector_id),),
        ).fetchone()
        if sector is None:
            raise ValueError("Setor não encontrado.")
        column = conn.execute(
            "SELECT id, name FROM org_queues WHERE id = ? AND sector_id = ? AND active = 1",
            (normalize_text(queue_id), sector["id"]),
        ).fetchone()
        if column is None:
            raise ValueError("Coluna não encontrada.")
        conn.execute(
            "UPDATE org_queues SET active = 0 WHERE id = ?",
            (column["id"],),
        )
        conn.execute(
            """
            UPDATE service_orders
            SET queue_id = 'analise', updated_at = ?
            WHERE queue_id = ? AND lower(sector) = lower(?)
            """,
            (_now(), column["id"], sector["name"]),
        )
    return column["name"]


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
