"""Agenda de treinamentos e de apresentações comerciais."""
import re
from calendar import monthrange
from datetime import date, timedelta

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.dependencies import require_auth
from app.services.legacy_core import normalize_text
from app.services.org_registry import (
    SCHEDULE_DAY_KEYS,
    TRAINING_SLOT_STARTS,
    get_person,
    list_people,
    slot_label,
)
from app.services.service_orders import (
    TRAINING_RESULT_LABELS,
    list_training_appointments,
    update_training_appointment,
)
from app.templating import render

router = APIRouter()

_MONTHS = (
    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
)
_WEEKDAYS = ("Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom")


def _anchor(raw: str) -> date:
    text = normalize_text(raw)[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return date.today()


def _url(view: str, day: date) -> str:
    return f"/agenda?vista={view}&data={day.isoformat()}"


def _shift_month(day: date, step: int) -> date:
    month = day.month + step
    year = day.year
    while month < 1:
        month += 12
        year -= 1
    while month > 12:
        month -= 12
        year += 1
    last = monthrange(year, month)[1]
    return date(year, month, min(day.day, last))


def _meeting_link(description: str) -> str:
    match = re.search(r"https?://\S+", description or "")
    if not match:
        return ""
    return match.group(0).rstrip(".,)")


def _slots_by_trainer() -> dict[str, list[dict]]:
    mapping: dict[str, list[dict]] = {}
    for person in list_people("treinador"):
        slots = (person.get("schedule") or {}).get("slots") or list(TRAINING_SLOT_STARTS)
        mapping[normalize_text(person.get("name")).lower()] = [
            {"value": slot, "label": slot_label(slot)} for slot in slots
        ]
    return mapping


def _event(item: dict, own: bool, today: date, slots_by_trainer: dict[str, list[dict]]) -> dict:
    scheduled = normalize_text(item.get("scheduled_date"))
    try:
        scheduled_day = date.fromisoformat(scheduled)
    except ValueError:
        scheduled_day = None
    result = normalize_text(item.get("training_result")).lower()
    if result not in TRAINING_RESULT_LABELS:
        result = ""
    hour = item.get("hour") or ""
    responsible = item.get("responsible") or ""
    slots = list(slots_by_trainer.get(normalize_text(responsible).lower()) or [
        {"value": slot, "label": slot_label(slot)} for slot in TRAINING_SLOT_STARTS
    ])
    if hour and hour not in {slot["value"] for slot in slots}:
        slots.append({"value": hour, "label": slot_label(hour)})
        slots.sort(key=lambda slot: slot["value"])
    return {
        "id": item.get("id") or "",
        "empresa": item.get("empresa") or "Cliente",
        "trainee": item.get("trainee") or "",
        "hour": hour,
        "hour_label": item.get("hour_label") or hour,
        "responsible": "" if own else responsible,
        "link": _meeting_link(item.get("description") or ""),
        "result": result,
        "result_label": TRAINING_RESULT_LABELS.get(result, ""),
        "reason": item.get("training_reason") or "",
        "scheduled_date": scheduled,
        "can_edit_schedule": bool(scheduled_day and today < scheduled_day),
        "slots": slots,
    }


def _trainer_frame(request: Request) -> tuple[list[str], list[str]]:
    kind = normalize_text(request.session.get("org_person_kind"))
    people = []
    if kind == "treinador":
        person = get_person(normalize_text(request.session.get("org_person_id")))
        if person:
            people = [person]
    else:
        people = list_people("treinador")
    slots: list[str] = []
    days: list[str] = []
    for person in people:
        schedule = person.get("schedule") or {}
        for slot in schedule.get("slots") or []:
            if slot not in slots:
                slots.append(slot)
        for day_key in schedule.get("days") or []:
            if day_key not in days:
                days.append(day_key)
    slots.sort()
    days.sort(key=lambda key: SCHEDULE_DAY_KEYS.index(key) if key in SCHEDULE_DAY_KEYS else 9)
    return slots, days


def _open_items(items: list[dict]) -> list[dict]:
    return [item for item in items if item.get("status") != "cancelada"]


def _commercial_name(request: Request) -> str:
    if not request.session.get("org_person_id"):
        return ""
    sector = normalize_text(request.session.get("org_sector_name"))
    if "comercial" not in sector.lower():
        return ""
    return normalize_text(request.session.get("org_person_name"))


@router.get("/agenda", response_class=HTMLResponse)
async def agenda_page(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect
    commercial_name = _commercial_name(request)
    kind = normalize_text(request.session.get("org_person_kind"))
    agenda_kind = "apresentacao" if commercial_name else "treinamento"
    if commercial_name:
        name = commercial_name
        own = True
    else:
        name = normalize_text(request.session.get("org_person_name")) if kind == "treinador" else ""
        own = kind == "treinador"
    view = normalize_text(request.query_params.get("vista")).lower()
    if view not in {"semana", "mes"}:
        view = "semana"
    anchor = _anchor(request.query_params.get("data", ""))
    today = date.today()
    if agenda_kind == "apresentacao":
        from app.services.service_orders import list_presentation_appointments

        items = list_presentation_appointments(name)
    else:
        items = _open_items(list_training_appointments(name))
    by_day: dict[str, list[dict]] = {}
    for item in items:
        day = normalize_text(item.get("scheduled_date"))
        if not day:
            continue
        by_day.setdefault(day, []).append(item)

<<<<<<< HEAD
    if agenda_kind == "apresentacao":
        from app.services.org_registry import TRAINING_SLOT_STARTS

        slots = list(TRAINING_SLOT_STARTS)
        work_days = list(SCHEDULE_DAY_KEYS[:5])
    else:
        slots, work_days = _trainer_frame(request)
=======
    slots, work_days = _trainer_frame(request)
    trainer_slots = _slots_by_trainer()
>>>>>>> ec54b6c6d10a38678d055534e896684b78cac14d
    weeks = []
    if view == "mes":
        prev_day = _shift_month(anchor.replace(day=1), -1)
        next_day = _shift_month(anchor.replace(day=1), 1)
        title = f"{_MONTHS[anchor.month - 1].capitalize()} de {anchor.year}"
        weeks = _month_grid(anchor, by_day, own, today, trainer_slots)
        week_days = []
        hours = []
    else:
        start = anchor - timedelta(days=anchor.weekday())
        prev_day = start - timedelta(days=7)
        next_day = start + timedelta(days=7)
        week_days, hours, title = _week_grid(start, slots, work_days, by_day, own, today, trainer_slots)

    return render(
        request,
        "agenda/index.html",
        {
            "active_page": "agenda",
            "agenda_kind": agenda_kind,
            "own_agenda": own,
            "trainer_name": name,
            "view": view,
            "title": title,
            "hours": hours,
            "week_days": week_days,
            "weeks": weeks,
            "prev_url": _url(view, prev_day),
            "next_url": _url(view, next_day),
            "today_url": _url(view, today),
            "week_url": _url("semana", anchor),
            "month_url": _url("mes", anchor),
            "anchor": anchor.isoformat(),
            "success": request.session.pop("agenda_success", ""),
            "error": request.session.pop("agenda_error", ""),
            "focus_id": request.session.pop("agenda_focus_id", ""),
        },
    )


@router.post("/agenda/{order_id}")
async def agenda_update(
    request: Request,
    order_id: str,
    result: str = Form(""),
    reason: str = Form(""),
    day: str = Form(""),
    hour: str = Form(""),
    vista: str = Form("semana"),
    data: str = Form(""),
):
    redirect = require_auth(request)
    if redirect:
        return redirect
    back = _url(
        normalize_text(vista).lower() if normalize_text(vista).lower() in {"semana", "mes"} else "semana",
        _anchor(data),
    )
    kind = normalize_text(request.session.get("org_person_kind"))
    author = normalize_text(request.session.get("org_person_name") or request.session.get("username")) or "Usuário"
    try:
        saved = update_training_appointment(
            order_id,
            result=result,
            reason=reason,
            day=day,
            hour=hour,
            author=author,
            actor_name=author,
            actor_is_trainer=kind == "treinador",
        )
        if saved.get("training_result") == "reagendou":
            request.session["agenda_success"] = (
                f"Treinamento reagendado para {saved.get('scheduled_date_label')}, {saved.get('hour_label')}."
            )
        else:
            label = saved.get("training_result_label") or "atualizado"
            request.session["agenda_success"] = f"Treinamento marcado como {label.lower()}."
    except ValueError as error:
        request.session["agenda_error"] = str(error)
        request.session["agenda_focus_id"] = order_id
    return RedirectResponse(url=back, status_code=303)


def _week_grid(
    start: date,
    slots: list[str],
    work_days: list[str],
    by_day: dict,
    own: bool,
    today: date,
    trainer_slots: dict,
):
    week = [start + timedelta(days=offset) for offset in range(7)]
    visible_keys = set(work_days)
    hours = list(slots)
    for day in week:
        iso = day.isoformat()
        for item in by_day.get(iso, []):
            key = SCHEDULE_DAY_KEYS[day.weekday()]
            visible_keys.add(key)
            hour = item.get("hour") or ""
            if hour and hour not in hours:
                hours.append(hour)
    hours.sort()
    if not visible_keys:
        visible_keys = set(SCHEDULE_DAY_KEYS[:5])
    columns = [day for day in week if SCHEDULE_DAY_KEYS[day.weekday()] in visible_keys]
    week_days = []
    for day in columns:
        iso = day.isoformat()
        grouped: dict[str, list[dict]] = {hour: [] for hour in hours}
        for item in by_day.get(iso, []):
            hour = item.get("hour") or ""
            if hour in grouped:
                grouped[hour].append(_event(item, own, today, trainer_slots))
        week_days.append(
            {
                "label": _WEEKDAYS[day.weekday()],
                "number": f"{day.day:02d}",
                "iso": iso,
                "is_today": day == today,
                "by_hour": grouped,
            }
        )
    if columns:
        title = _range_title(columns[0], columns[-1])
    else:
        title = _range_title(start, start + timedelta(days=4))
    hour_rows = [{"value": hour, "label": slot_label(hour)} for hour in hours]
    return week_days, hour_rows, title


def _range_title(first: date, last: date) -> str:
    if first.month == last.month and first.year == last.year:
        return f"{first.day:02d} – {last.day:02d} de {_MONTHS[first.month - 1]} de {first.year}"
    if first.year == last.year:
        return (
            f"{first.day:02d} de {_MONTHS[first.month - 1][:3]} – "
            f"{last.day:02d} de {_MONTHS[last.month - 1]} de {first.year}"
        )
    return f"{first.strftime('%d/%m/%Y')} – {last.strftime('%d/%m/%Y')}"


def _month_grid(anchor: date, by_day: dict, own: bool, today: date, trainer_slots: dict) -> list[list[dict]]:
    first = anchor.replace(day=1)
    end = date(anchor.year, anchor.month, monthrange(anchor.year, anchor.month)[1])
    cursor = first - timedelta(days=first.weekday())
    weeks = []
    while cursor <= end:
        week = []
        for _ in range(7):
            iso = cursor.isoformat()
            events = [_event(item, own, today, trainer_slots) for item in by_day.get(iso, [])]
            events.sort(key=lambda item: item.get("hour") or "")
            week.append(
                {
                    "number": cursor.day,
                    "in_month": cursor.month == anchor.month,
                    "is_today": cursor == today,
                    "events": events,
                }
            )
            cursor += timedelta(days=1)
        weeks.append(week)
    return weeks
