"""Agenda dos treinamentos marcados para cada treinador."""
import re
from calendar import monthrange
from datetime import date, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.dependencies import require_auth
from app.services.legacy_core import normalize_text
from app.services.org_registry import SCHEDULE_DAY_KEYS, get_person, list_people, slot_label
from app.services.service_orders import list_training_appointments
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


def _event(item: dict, own: bool) -> dict:
    return {
        "empresa": item.get("empresa") or "Cliente",
        "trainee": item.get("trainee") or "",
        "hour": item.get("hour") or "",
        "hour_label": item.get("hour_label") or item.get("hour") or "",
        "responsible": "" if own else (item.get("responsible") or ""),
        "link": _meeting_link(item.get("description") or ""),
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


@router.get("/agenda", response_class=HTMLResponse)
async def agenda_page(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect
    kind = normalize_text(request.session.get("org_person_kind"))
    name = normalize_text(request.session.get("org_person_name")) if kind == "treinador" else ""
    own = kind == "treinador"
    view = normalize_text(request.query_params.get("vista")).lower()
    if view not in {"semana", "mes"}:
        view = "semana"
    anchor = _anchor(request.query_params.get("data", ""))
    today = date.today()
    items = _open_items(list_training_appointments(name))
    by_day: dict[str, list[dict]] = {}
    for item in items:
        day = normalize_text(item.get("scheduled_date"))
        if not day:
            continue
        by_day.setdefault(day, []).append(item)

    slots, work_days = _trainer_frame(request)
    weeks = []
    if view == "mes":
        prev_day = _shift_month(anchor.replace(day=1), -1)
        next_day = _shift_month(anchor.replace(day=1), 1)
        title = f"{_MONTHS[anchor.month - 1].capitalize()} de {anchor.year}"
        weeks = _month_grid(anchor, by_day, own)
        week_days = []
        hours = []
    else:
        start = anchor - timedelta(days=anchor.weekday())
        prev_day = start - timedelta(days=7)
        next_day = start + timedelta(days=7)
        week_days, hours, title = _week_grid(start, slots, work_days, by_day, own)

    return render(
        request,
        "agenda/index.html",
        {
            "active_page": "agenda",
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
        },
    )


def _week_grid(start: date, slots: list[str], work_days: list[str], by_day: dict, own: bool):
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
    today = date.today()
    week_days = []
    for day in columns:
        iso = day.isoformat()
        grouped: dict[str, list[dict]] = {hour: [] for hour in hours}
        for item in by_day.get(iso, []):
            hour = item.get("hour") or ""
            if hour in grouped:
                grouped[hour].append(_event(item, own))
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


def _month_grid(anchor: date, by_day: dict, own: bool) -> list[list[dict]]:
    today = date.today()
    first = anchor.replace(day=1)
    end = date(anchor.year, anchor.month, monthrange(anchor.year, anchor.month)[1])
    cursor = first - timedelta(days=first.weekday())
    weeks = []
    while cursor <= end:
        week = []
        for _ in range(7):
            iso = cursor.isoformat()
            events = [_event(item, own) for item in by_day.get(iso, [])]
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
