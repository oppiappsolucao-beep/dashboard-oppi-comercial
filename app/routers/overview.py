from datetime import date

from config.crm_options import (
    ACTIVITY_RESULT_OPTIONS,
    CHANNEL_LABEL_TO_KEY,
    CHANNEL_OPTIONS,
    LOST_REASON_OPTIONS,
    NEXT_ACTION_OPTIONS,
    PIPELINE_STAGE_OPTIONS,
    PROCESS_ACTION_OPTIONS,
)
from fastapi import APIRouter, Form, Request
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.dependencies import get_prepared_data, is_admin, require_auth
from app.services.crm_validation_service import (
    normalize_legacy_action,
    normalize_legacy_next_action,
    normalize_legacy_result,
    normalize_legacy_stage,
    normalize_opportunity_status,
    resolve_pipeline_stage,
    suggest_from_result,
    validate_activity_payload,
)
from app.services.legacy_core import invalidate_sheet_cache, normalize_text, status_group
from app.services.filters import (
    DashboardFilters,
    apply_default_period_filters,
    get_filter_options,
    parse_dashboard_filters,
)
from app.services.followup_service import (
    OperationalFilters,
    apply_seller_scope,
    build_operational_overview_context,
    parse_operational_filters,
)
from app.services.lead_actions_storage import DEFAULT_TENANT_ID, complete_activity, get_lead_action
from app.templating import render

router = APIRouter()


def _overview_context(request: Request, filters: DashboardFilters, operational: OperationalFilters, success: str = ""):
    df, columns = get_prepared_data()
    options = get_filter_options(df)
    filters = apply_default_period_filters(filters, df)
    filters = apply_seller_scope(request, filters, options["seller_options"], is_admin(request))

    operational_ctx = build_operational_overview_context(
        df,
        columns,
        filters,
        operational,
        tenant_id=DEFAULT_TENANT_ID,
    )

    return {
        "active_page": "overview",
        "success": success or request.session.pop("company_registration_success", "") or request.session.pop("overview_success", ""),
        "overview_error": request.session.pop("overview_error", ""),
        "filters": filters,
        "options": options,
        "columns": columns,
        "is_admin": is_admin(request),
        "current_user": normalize_text(request.session.get("username", "")),
        **operational_ctx,
    }


@router.get("/visao-geral", response_class=HTMLResponse)
async def overview_page(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect
    filters = parse_dashboard_filters(request)
    operational = parse_operational_filters(request)
    return render(request, "overview.html", _overview_context(request, filters, operational))


@router.post("/visao-geral/filtros", response_class=HTMLResponse)
async def overview_filters(
    request: Request,
    seller: str = Form("Todos os vendedores"),
    status: str = Form("Todos os status"),
    period_start: str = Form(""),
    period_end: str = Form(""),
    niche: str = Form("Todos os nichos"),
    state: str = Form("Todos os estados"),
    search: str = Form(""),
    op_priority: str = Form("Todas"),
    op_stage: str = Form("Todas as etapas"),
    op_action_type: str = Form("Todos os tipos"),
    op_status: str = Form("Todos"),
    op_channel: str = Form("Todos os canais"),
    overdue_only: str = Form(""),
    no_next_action_only: str = Form(""),
):
    redirect = require_auth(request)
    if redirect:
        return redirect

    filters = parse_dashboard_filters(request, {
        "seller": seller,
        "status": status,
        "period_start": period_start,
        "period_end": period_end,
        "niche": niche,
        "state": state,
        "search": search,
    })
    operational = parse_operational_filters(request, {
        "op_priority": op_priority,
        "op_stage": op_stage,
        "op_action_type": op_action_type,
        "op_status": op_status,
        "op_channel": op_channel,
        "overdue_only": overdue_only,
        "no_next_action_only": no_next_action_only,
    })
    ctx = _overview_context(request, filters, operational)
    return render(request, "partials/overview_content.html", ctx)


@router.post("/visao-geral/atualizar")
async def overview_refresh(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect
    invalidate_sheet_cache()
    return RedirectResponse(url="/visao-geral", status_code=303)


@router.get("/visao-geral/acoes/{sheet_row}/concluir", response_class=HTMLResponse)
async def overview_complete_action_form(request: Request, sheet_row: int):
    redirect = require_auth(request)
    if redirect:
        return redirect

    df, columns = get_prepared_data()
    row_match = df[df["_sheet_row"] == sheet_row]
    if row_match.empty:
        return RedirectResponse(url="/visao-geral", status_code=303)

    row = row_match.iloc[0]
    stored = get_lead_action(DEFAULT_TENANT_ID, sheet_row) or {}
    grouped = status_group(row.get("_status_grupo") or row.get("_status_original", ""))
    current_stage = resolve_pipeline_stage(grouped, stored)
    stored_next_action = normalize_legacy_next_action(stored.get("next_action_description")) or stored.get("next_action_description", "")
    return render(
        request,
        "overview_complete_action.html",
        {
            "active_page": "overview",
            "sheet_row": sheet_row,
            "empresa": normalize_text(row.get("_empresa", "")) or "—",
            "stored": stored,
            "stored_next_action": stored_next_action,
            "today": date.today().isoformat(),
            "overview_error": request.session.pop("overview_error", ""),
            "result_options": [opt for opt in ACTIVITY_RESULT_OPTIONS if opt != "Selecione"],
            "process_actions": NEXT_ACTION_OPTIONS,
            "next_action_options": NEXT_ACTION_OPTIONS,
            "stage_options": PIPELINE_STAGE_OPTIONS,
            "channel_options": CHANNEL_OPTIONS,
            "channel_keys": CHANNEL_LABEL_TO_KEY,
            "lost_reason_options": LOST_REASON_OPTIONS,
            "current_stage": current_stage,
        },
    )


@router.post("/visao-geral/acoes/{sheet_row}/concluir")
async def overview_complete_action_submit(
    request: Request,
    sheet_row: int,
    result: str = Form(...),
    note: str = Form(""),
    next_action_date: str = Form(""),
    next_action_time: str = Form("09:00"),
    next_action_type: str = Form("whatsapp"),
    next_action_description: str = Form(""),
    move_stage: str = Form(""),
    lead_closed: str = Form(""),
    lost_reason: str = Form(""),
):
    redirect = require_auth(request)
    if redirect:
        return redirect

    user = normalize_text(request.session.get("username", "")) or "Usuário"
    result = normalize_legacy_result(result)
    next_action_description = normalize_legacy_next_action(next_action_description)
    move_stage = normalize_legacy_stage(move_stage)

    validation_error = validate_activity_payload({
        "result": result,
        "next_action": next_action_description,
        "move_stage": move_stage,
    })
    if validation_error:
        request.session["overview_error"] = validation_error
        return RedirectResponse(url=f"/visao-geral/acoes/{sheet_row}/concluir", status_code=303)

    if not result:
        request.session["overview_error"] = "Selecione o resultado para concluir a atividade."
        return RedirectResponse(url=f"/visao-geral/acoes/{sheet_row}/concluir", status_code=303)

    parsed_date = None
    if next_action_date:
        try:
            parsed_date = date.fromisoformat(next_action_date)
        except ValueError:
            parsed_date = None

    if not lead_closed and not parsed_date and result not in {"Sem interesse", "Contato inválido"}:
        request.session["overview_error"] = "Defina a próxima ação antes de concluir."
        return RedirectResponse(url=f"/visao-geral/acoes/{sheet_row}/concluir", status_code=303)

    if lead_closed and not lost_reason and result == "Sem interesse":
        request.session["overview_error"] = "Informe o motivo da perda."
        return RedirectResponse(url=f"/visao-geral/acoes/{sheet_row}/concluir", status_code=303)

    df, _columns = get_prepared_data()
    row_match = df[df["_sheet_row"] == sheet_row]
    stored = get_lead_action(DEFAULT_TENANT_ID, sheet_row) or {}
    current_stage = ""
    if not row_match.empty:
        grouped = status_group(row_match.iloc[0].get("_status_grupo") or row_match.iloc[0].get("_status_original", ""))
        current_stage = resolve_pipeline_stage(grouped, stored)

    suggestion = suggest_from_result(result, current_stage)
    if not move_stage:
        move_stage = suggestion.get("move_stage", "")
    if not next_action_description and not lead_closed:
        next_action_description = suggestion.get("next_action", "")

    opportunity_status = suggestion.get("opportunity_status", "")
    if lead_closed:
        opportunity_status = "Fechada perdida"

    complete_activity(
        DEFAULT_TENANT_ID,
        sheet_row,
        result=result,
        note=note,
        user=user,
        next_action_date=parsed_date,
        next_action_time=next_action_time,
        next_action_type=next_action_type,
        next_action_description=next_action_description,
        move_stage=move_stage,
        opportunity_status=opportunity_status,
        lost_reason=lost_reason,
    )
    request.session["overview_success"] = "Atividade concluída e histórico atualizado."
    return RedirectResponse(url="/visao-geral", status_code=303)
