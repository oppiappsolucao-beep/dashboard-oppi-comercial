from datetime import date
import json

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from app.dependencies import get_prepared_data, is_admin, require_auth
from app.services.activity_service import (
    ActivitiesViewParams,
    atualizar_atividade_inline,
    build_activity_detail_panel,
    build_activity_page_context,
    build_activity_timeline_for_activity,
    build_new_activity_modal_context,
    buscar_acoes_por_etapa,
    buscar_leads_para_atividade,
    cancelar_atividade,
    criar_atividade,
    mover_atividade_kanban,
    scope_df_for_lead_search,
    sugerir_fluxo_por_resultado,
    atualizar_proxima_acao_atividade,
    _serialize_activity,
)
from app.services.activities_storage import DEFAULT_TENANT_ID, get_activity, soft_delete_activity
from app.services.filters import apply_default_period_filters, apply_dashboard_filters, get_filter_options, parse_dashboard_filters
from app.services.followup_service import apply_seller_scope
from app.services.legacy_core import invalidate_sheet_cache, normalize_text
from app.templating import render

router = APIRouter()


def _parse_activities_params(request: Request, form: dict | None = None) -> ActivitiesViewParams:
    data = form or {}
    if not data and request.query_params:
        data = dict(request.query_params)

    try:
        page = int(data.get("page", 1))
    except (TypeError, ValueError):
        page = 1
    try:
        per_page = int(data.get("per_page", 10))
    except (TypeError, ValueError):
        per_page = 10

    tab = data.get("tab", "todas")
    return ActivitiesViewParams(
        tab=tab if tab in ("todas", "pendentes", "concluidas", "atrasadas") else "todas",
        activity_type=data.get("activity_type", "Todos os tipos"),
        channel=data.get("channel", "Todos os canais"),
        responsible=data.get("responsible", "Todos os responsáveis"),
        stage=data.get("stage", "Todas as etapas"),
        page=max(1, page),
        per_page=per_page if per_page in (10, 25, 50) else 10,
    )


def _activities_context(request: Request, filters, activities_params: ActivitiesViewParams, success: str = "", error: str = ""):
    df, columns = get_prepared_data()
    options = get_filter_options(df)
    filters = apply_default_period_filters(filters, df)
    filters = apply_seller_scope(request, filters, options["seller_options"], is_admin(request))

    ctx = build_activity_page_context(df, columns, filters, activities_params, DEFAULT_TENANT_ID)
    responsible_options = ["Todos os responsáveis"] + options["seller_options"]

    return {
        "active_page": "activities",
        "filters": filters,
        "options": options,
        "activities_params": activities_params,
        "responsible_options": responsible_options,
        "is_admin": is_admin(request),
        "current_user": normalize_text(request.session.get("username", "")),
        "success": success or request.session.pop("activities_success", ""),
        "error": error or request.session.pop("activities_error", ""),
        **ctx,
    }


def _render_content(request, filters, activities_params, success="", error=""):
    return render(
        request,
        "partials/activities_content.html",
        _activities_context(request, filters, activities_params, success, error),
    )


def _modal_context(
    request: Request,
    error: str = "",
    *,
    return_to: str = "",
    prefill_sheet_row: int | None = None,
    prefill_empresa: str | None = None,
):
    df, columns = get_prepared_data()
    options = get_filter_options(df)
    filters = apply_default_period_filters(parse_dashboard_filters(request), df)
    filters = apply_seller_scope(request, filters, options["seller_options"], is_admin(request))
    current_user = normalize_text(request.session.get("username", "")) or "Usuário"
    if prefill_sheet_row is None:
        try:
            prefill_sheet_row = int(request.query_params.get("sheet_row") or 0)
        except (TypeError, ValueError):
            prefill_sheet_row = 0
    if prefill_empresa is None:
        prefill_empresa = normalize_text(request.query_params.get("empresa", ""))
    safe_return = normalize_text(return_to) or normalize_text(request.query_params.get("return_to", ""))
    return build_new_activity_modal_context(
        seller_options=options["seller_options"],
        current_user=current_user,
        is_admin_user=is_admin(request),
        today_iso=date.today().isoformat(),
        error=error,
        prefill_sheet_row=int(prefill_sheet_row or 0),
        prefill_empresa=normalize_text(prefill_empresa),
        return_to=safe_return,
    )


def _os_board_context(request: Request) -> dict:
    from app.dependencies import get_session_user
    from app.services.kanban_summary import build_kanban_summary, pick_support_sector, support_level
    from app.services.org_registry import add_sector_queue, list_sectors
    from app.services.service_orders import build_sector_board

    sectors = list_sectors()
    user = get_session_user(request) or {}
    viewer = " ".join(
        normalize_text(part)
        for part in (
            user.get("department_name"),
            user.get("name"),
            user.get("username"),
            request.session.get("org_sector_name"),
            request.session.get("org_person_name"),
        )
        if normalize_text(part)
    )
    viewer_level = support_level(viewer)
    employee = bool(request.session.get("org_person_id"))
    if employee:
        sector_id = normalize_text(request.session.get("org_sector_id"))
        sector_name = normalize_text(request.session.get("org_sector_name"))
    else:
        sector_id = normalize_text(request.query_params.get("setor"))
        chosen = next((item for item in sectors if item["id"] == sector_id), None)
        if chosen is None and sectors:
            chosen = sectors[0]
            sector_id = chosen["id"]
        sector_name = chosen["name"] if chosen else ""
    if viewer_level and not is_admin(request):
        folded = sector_name.lower()
        on_support_board = "suporte" in folded or "nível" in folded or "nivel" in folded
        current_level = support_level(sector_name)
        if not on_support_board or (current_level is not None and current_level != viewer_level):
            preferred = pick_support_sector(sectors, viewer_level)
            if preferred:
                sector_id = preferred["id"]
                sector_name = preferred["name"]
    inicio = normalize_text(request.query_params.get("inicio"))
    fim = normalize_text(request.query_params.get("fim"))
    summary = build_kanban_summary(sector_name, inicio, fim, viewer=viewer)
    columns = (
        build_sector_board(sector_id, sector_name, summary["inicio"], summary["fim"])
        if sector_id
        else []
    )
    trainer_name = _logged_trainer_name(request)
    if trainer_name:
        for column in columns:
            column["cards"] = [
                card
                for card in column["cards"]
                if normalize_text(card.get("responsible")).lower() == trainer_name
            ]
    if summary.get("layout") != "suporte":
        aberto = 0
        feito = 0
        for column in columns:
            total = len(column.get("cards") or [])
            if column.get("id") == "concluida":
                feito += total
            elif column.get("id") != "campanha":
                aberto += total
        summary["andamento"] = aberto
        summary["andamento_note"] = "Ordens nas colunas em aberto deste quadro."
        summary["concluidos"] = feito
        summary["concluidos_note"] = "Ordens na coluna Concluída."
    return {
        "active_page": "activities",
        "is_admin": not employee,
        "sectors": sectors,
        "sector_id": sector_id,
        "sector_name": sector_name,
        "columns": columns,
        "can_manage_queues": bool(sector_id),
        "inicio": summary["inicio"],
        "fim": summary["fim"],
        "summary": summary,
        "is_commercial": "comercial" in sector_name.lower(),
        "can_delete_orders": _oppi_tech_board(request, sector_name),
        "success": request.session.pop("os_board_success", ""),
        "error": request.session.pop("os_board_error", ""),
    }


@router.get("/atividades", response_class=HTMLResponse)
async def activities_page(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect
    return render(request, "activities/os_board.html", _os_board_context(request))


@router.get("/proposta", response_class=HTMLResponse)
async def proposal_page(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect
    from app.services.org_registry import list_sectors
    from app.services.service_orders import build_sector_board, is_commercial_sector

    cards = []
    sector_name = ""
    for sector in list_sectors():
        if not is_commercial_sector(sector.get("name") or ""):
            continue
        sector_name = sector["name"]
        columns = build_sector_board(sector["id"], sector_name)
        cards = [
            card
            for column in columns
            if "proposta" in (column.get("name") or "").lower()
            for card in column.get("cards") or []
        ]
        break
    return render(
        request,
        "activities/proposal_list.html",
        {
            "active_page": "proposta",
            "sector_name": sector_name,
            "cards": cards,
        },
    )


@router.post("/atividades/filas")
async def activities_add_queue(
    request: Request,
    sector_id: str = Form(""),
    queue_name: str = Form(""),
    inicio: str = Form(""),
    fim: str = Form(""),
):
    redirect = require_auth(request)
    if redirect:
        return redirect
    from app.services.org_registry import add_sector_queue

    employee_sector = normalize_text(request.session.get("org_sector_id"))
    target = employee_sector or normalize_text(sector_id)
    if employee_sector and normalize_text(sector_id) not in {"", employee_sector}:
        request.session["os_board_error"] = "Você só cria filas do seu setor."
        return _board_redirect(employee_sector, True, inicio, fim)
    try:
        add_sector_queue(target, queue_name)
    except ValueError as error:
        request.session["os_board_error"] = str(error)
    else:
        request.session["os_board_success"] = "Fila criada."
    return _board_redirect(target, bool(employee_sector), inicio, fim)


def _board_redirect(sector_id: str, employee: bool, inicio: str = "", fim: str = ""):
    from urllib.parse import urlencode

    params = {}
    if not employee and sector_id:
        params["setor"] = sector_id
    if normalize_text(inicio):
        params["inicio"] = normalize_text(inicio)
    if normalize_text(fim):
        params["fim"] = normalize_text(fim)
    query = f"?{urlencode(params)}" if params else ""
    return RedirectResponse(url=f"/atividades{query}", status_code=303)


@router.post("/atividades/filas/{queue_id}/mover")
async def activities_move_queue(
    request: Request,
    queue_id: str,
    sector_id: str = Form(""),
    direction: str = Form("direita"),
    inicio: str = Form(""),
    fim: str = Form(""),
):
    redirect = require_auth(request)
    if redirect:
        return redirect
    from app.services.org_registry import move_sector_queue

    employee_sector = normalize_text(request.session.get("org_sector_id"))
    target = employee_sector or normalize_text(sector_id)
    if employee_sector and normalize_text(sector_id) not in {"", employee_sector}:
        request.session["os_board_error"] = "Você só organiza as colunas do seu setor."
        return _board_redirect(employee_sector, True, inicio, fim)
    try:
        move_sector_queue(target, queue_id, direction)
    except ValueError as error:
        request.session["os_board_error"] = str(error)
    return _board_redirect(target, bool(employee_sector), inicio, fim)


@router.post("/atividades/filas/{queue_id}/remover")
async def activities_remove_queue(
    request: Request,
    queue_id: str,
    sector_id: str = Form(""),
    inicio: str = Form(""),
    fim: str = Form(""),
):
    redirect = require_auth(request)
    if redirect:
        return redirect
    from app.services.org_registry import remove_sector_queue

    employee_sector = normalize_text(request.session.get("org_sector_id"))
    target = employee_sector or normalize_text(sector_id)
    if employee_sector and normalize_text(sector_id) not in {"", employee_sector}:
        request.session["os_board_error"] = "Você só exclui colunas do seu setor."
        return _board_redirect(employee_sector, True, inicio, fim)
    try:
        name = remove_sector_queue(target, queue_id)
    except ValueError as error:
        request.session["os_board_error"] = str(error)
    else:
        request.session["os_board_success"] = f"Coluna {name} excluída. As ordens dela voltaram para Análise."
    return _board_redirect(target, bool(employee_sector), inicio, fim)


@router.post("/atividades/os/{order_id}/fila")
async def activities_move_order(
    request: Request,
    order_id: str,
    queue_id: str = Form(""),
    sector_id: str = Form(""),
    reopen: str = Form(""),
):
    redirect = require_auth(request)
    if redirect:
        return redirect
    from app.services.org_registry import list_sectors
    from app.services.service_orders import get_order_detail, move_service_order

    detail = get_order_detail(order_id)
    if not detail or not _order_visible(request, detail):
        return HTMLResponse("Ordem de serviço não encontrada.", status_code=404)
    employee_sector = normalize_text(request.session.get("org_sector_id"))
    target = employee_sector or normalize_text(sector_id)
    sectors = list_sectors()
    sector = next((item for item in sectors if item["id"] == target), None)
    if sector is None:
        return HTMLResponse("Setor não encontrado.", status_code=404)
    try:
        move_service_order(
            order_id,
            queue_id,
            sector["id"],
            sector["name"],
            author=_os_actor(request),
            reopen=normalize_text(reopen) in {"1", "true", "sim", "yes"},
        )
    except ValueError as error:
        return HTMLResponse(str(error), status_code=400)
    from app.services.org_registry import list_sector_queues

    queue_name = next(
        (
            item["name"]
            for item in list_sector_queues(sector["id"])
            if item["id"] == normalize_text(queue_id)
        ),
        "",
    )
    if "proposta" in queue_name.lower():
        return HTMLResponse(f"/atividades/os/{order_id}/proposta")
    if normalize_text(queue_id) == "concluida":
        from app.services.campaign_leads import open_lead_cadastro_url
        from app.services.service_orders import is_commercial_sector, is_oppi_tech_sector

        if is_commercial_sector(sector["name"]) or is_oppi_tech_sector(sector["name"]):
            target = open_lead_cadastro_url(detail)
            if target.startswith("/cadastro/"):
                return HTMLResponse(target)
    return HTMLResponse("ok")


@router.post("/atividades/os/{order_id}/excluir")
async def activities_delete_order(request: Request, order_id: str):
    redirect = require_auth(request)
    if redirect:
        return redirect
    from app.services.service_orders import delete_service_order, get_order_detail

    detail = get_order_detail(order_id)
    if not detail or not _order_visible(request, detail):
        return HTMLResponse("Ordem de serviço não encontrada.", status_code=404)
    if not _oppi_tech_board(request, detail.get("sector") or ""):
        return HTMLResponse("Somente o acesso Oppi Tech pode excluir o card.", status_code=403)
    try:
        delete_service_order(order_id)
    except ValueError as error:
        return HTMLResponse(str(error), status_code=400)
    return HTMLResponse("ok")


def _os_actor(request: Request) -> str:
    return (
        normalize_text(request.session.get("org_person_name"))
        or normalize_text(request.session.get("username"))
        or "Usuário"
    )


def _matches_oppi_tech_access(value: str) -> bool:
    """Oppi Tech, e o login Oppi que abre todas as telas da solução."""
    import re
    import unicodedata

    from app.services.service_orders import is_oppi_tech_sector

    if is_oppi_tech_sector(value or ""):
        return True
    text = unicodedata.normalize("NFKD", normalize_text(value).lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    compact = re.sub(r"[^a-z0-9]", "", text)
    return compact in {"oppi", "oppitech", "opitech"}


def _oppi_tech_login(request: Request) -> bool:
    """Acesso Oppi Tech: funcionário desse setor, ou o login Oppi/Oppi Tech com todas as telas."""
    from app.dependencies import get_session_user

    if request.session.get("org_person_id"):
        return _matches_oppi_tech_access(request.session.get("org_sector_name") or "")

    user = get_session_user(request) or {}
    return any(
        _matches_oppi_tech_access(value or "")
        for value in (
            request.session.get("username"),
            user.get("name"),
            user.get("username"),
            user.get("department_name"),
        )
    )


def _oppi_tech_board(request: Request, _sector_name: str = "") -> bool:
    """Excluir o card do kanban só para quem entrou pelo acesso Oppi Tech."""
    return _oppi_tech_login(request)


def _order_panel_context(order: dict, sector_notice: str = "", can_delete_order: bool = False) -> dict:
    from app.services.org_registry import list_sectors
    from app.services.service_orders import queue_choices

    sector_id, queues = queue_choices(order.get("sector") or "")
    return {
        "order": order,
        "order_sectors": list_sectors(),
        "sector_notice": sector_notice,
        "order_sector_id": sector_id,
        "order_queues": queues,
        "can_delete_order": can_delete_order,
    }


def _logged_trainer_name(request: Request) -> str:
    if normalize_text(request.session.get("org_person_kind")) != "treinador":
        return ""
    return normalize_text(request.session.get("org_person_name")).lower()


def _order_visible(request: Request, detail: dict) -> bool:
    if not request.session.get("org_person_id"):
        return True
    sector_name = normalize_text(request.session.get("org_sector_name"))
    if sector_name.lower() != normalize_text(detail.get("sector")).lower():
        return False
    trainer_name = _logged_trainer_name(request)
    if trainer_name and normalize_text(detail.get("responsible")).lower() != trainer_name:
        return False
    return True


@router.get("/atividades/os/{order_id}", response_class=HTMLResponse)
async def activities_order_detail(request: Request, order_id: str):
    redirect = require_auth(request)
    if redirect:
        return redirect
    from app.services.service_orders import get_order_detail

    detail = get_order_detail(order_id)
    if not detail or not _order_visible(request, detail):
        return HTMLResponse("Ordem de serviço não encontrada.", status_code=404)
    return render(
        request,
        "partials/os_order_panel.html",
        _order_panel_context(
            detail,
            can_delete_order=_oppi_tech_board(request, detail.get("sector") or ""),
        ),
    )


@router.post("/atividades/os/{order_id}/atualizacao", response_class=HTMLResponse)
async def activities_order_update(request: Request, order_id: str, note: str = Form("")):
    redirect = require_auth(request)
    if redirect:
        return redirect
    from app.services.service_orders import add_order_update, get_order_detail

    detail = get_order_detail(order_id)
    if not detail or not _order_visible(request, detail):
        return HTMLResponse("Ordem de serviço não encontrada.", status_code=404)
    try:
        add_order_update(order_id, note, _os_actor(request))
    except ValueError as error:
        return HTMLResponse(str(error), status_code=400)
    detail = get_order_detail(order_id)
    return render(
        request,
        "partials/os_order_panel.html",
        _order_panel_context(
            detail,
            can_delete_order=_oppi_tech_board(request, detail.get("sector") or ""),
        ),
    )


@router.post("/atividades/os/{order_id}/setor", response_class=HTMLResponse)
async def activities_direct_sector(request: Request, order_id: str, sector_name: str = Form("")):
    redirect = require_auth(request)
    if redirect:
        return redirect
    from app.services.service_orders import get_order_detail, send_order_to_sector
    from app.services.ticket_orders import ticket_card_extra, write_directed_sector

    detail = get_order_detail(order_id)
    if not detail or not _order_visible(request, detail):
        return HTMLResponse("Ordem de serviço não encontrada.", status_code=404)
    try:
        notice = send_order_to_sector(order_id, sector_name, _os_actor(request))
    except ValueError as error:
        return render(
            request,
            "partials/os_order_panel.html",
            _order_panel_context(
                detail,
                str(error),
                can_delete_order=_oppi_tech_board(request, detail.get("sector") or ""),
            ),
        )
    extra = ticket_card_extra(order_id) or {}
    sheet_note = ""
    if extra.get("sheet_row_ticket"):
        sheet_note = write_directed_sector(int(extra["sheet_row_ticket"]), normalize_text(sector_name))
    request.session["os_board_success"] = sheet_note or notice or f"OS encaminhada para {normalize_text(sector_name)}."
    return HTMLResponse("ok")


def _proposal_values(order: dict, form: dict | None = None) -> dict:
    client = order.get("client") or {}
    values = {
        "razao_social": order.get("empresa") or "",
        "cnpj": "",
        "endereco": client.get("endereco") or "",
        "email": client.get("email") or "",
        "responsavel": client.get("contato") or "",
        "cargo": "",
        "telefone": client.get("whatsapp") or client.get("telefone") or "",
        "nome_fantasia": "",
        "colaboradores": "",
        "plan_key": "boleto",
        "valor_boleto": "",
        "valor_cartao": "",
        "valor_anual": "",
        "valor_mensal_equivalente": "",
        "valor_adicional": "",
        "valor_final": "",
        "observacao": "",
    }
    try:
        df, columns = get_prepared_data()
        from app.services.proposal_commercial_pdf import collect_client_data

        found = collect_client_data(values["razao_social"], df, columns)
        for key in ("cnpj", "endereco", "email", "responsavel", "cargo", "nome_fantasia"):
            if found.get(key) and not values.get(key):
                values[key] = found[key]
        if found.get("whatsapp") and not values["telefone"]:
            values["telefone"] = found["whatsapp"]
        if found.get("colaboradores"):
            values["colaboradores"] = found["colaboradores"]
    except Exception:
        pass
    typed_keys = {
        "colaboradores",
        "plan_key",
        "valor_boleto",
        "valor_cartao",
        "valor_anual",
        "valor_mensal_equivalente",
        "valor_adicional",
        "valor_final",
        "observacao",
    }
    if form:
        for key in values:
            posted = normalize_text(form.get(key))
            if posted or key in typed_keys:
                values[key] = posted
    return values


def _proposal_pdf_bytes(order: dict, values: dict) -> tuple[bytes, str]:
    from app.services.proposal_commercial_pdf import generate_commercial_proposal_pdf, proposal_pdf_filename

    try:
        df, columns = get_prepared_data()
    except Exception:
        import pandas as pd

        df, columns = pd.DataFrame(), {}
    try:
        colaboradores = int(normalize_text(values.get("colaboradores")) or "0")
    except ValueError:
        colaboradores = 0
    pdf = generate_commercial_proposal_pdf(
        values.get("razao_social") or order.get("empresa") or "Cliente",
        df,
        columns or {},
        proposal_snapshot={
            "colaboradores": colaboradores,
            "plan_key": values.get("plan_key") or "boleto",
            "manual": True,
            "valor_boleto": values.get("valor_boleto") or "",
            "valor_cartao": values.get("valor_cartao") or "",
            "valor_anual": values.get("valor_anual") or "",
            "valor_mensal_equivalente": values.get("valor_mensal_equivalente") or "",
            "valor_adicional": values.get("valor_adicional") or "",
            "valor_final": values.get("valor_final") or "",
            "observacao": values.get("observacao") or "",
        },
        client_override={
            "razao_social": values.get("razao_social"),
            "empresa": values.get("razao_social"),
            "cnpj": values.get("cnpj"),
            "documento": values.get("cnpj"),
            "endereco": values.get("endereco"),
            "email": values.get("email"),
            "responsavel": values.get("responsavel"),
            "cargo": values.get("cargo"),
            "telefone": values.get("telefone"),
            "whatsapp": values.get("telefone"),
            "nome_fantasia": values.get("nome_fantasia"),
        },
    )
    return pdf, proposal_pdf_filename(values.get("razao_social") or order.get("empresa") or "Cliente")


@router.get("/atividades/os/{order_id}/proposta", response_class=HTMLResponse)
async def activities_proposal_form(request: Request, order_id: str):
    redirect = require_auth(request)
    if redirect:
        return redirect
    from app.services.service_orders import get_order_detail

    detail = get_order_detail(order_id)
    if not detail or not _order_visible(request, detail):
        return HTMLResponse("Ordem de serviço não encontrada.", status_code=404)
    return render(
        request,
        "activities/proposal_form.html",
        {
            "active_page": "activities",
            "order": detail,
            "values": _proposal_values(detail),
            "error": "",
        },
    )


@router.post("/atividades/os/{order_id}/proposta/pdf")
async def activities_proposal_pdf(
    request: Request,
    order_id: str,
    razao_social: str = Form(""),
    cnpj: str = Form(""),
    endereco: str = Form(""),
    email: str = Form(""),
    responsavel: str = Form(""),
    cargo: str = Form(""),
    telefone: str = Form(""),
    nome_fantasia: str = Form(""),
    colaboradores: str = Form(""),
    plan_key: str = Form("boleto"),
    valor_boleto: str = Form(""),
    valor_cartao: str = Form(""),
    valor_anual: str = Form(""),
    valor_mensal_equivalente: str = Form(""),
    valor_adicional: str = Form(""),
    valor_final: str = Form(""),
    observacao: str = Form(""),
    disposicao: str = Form("anexo"),
):
    redirect = require_auth(request)
    if redirect:
        return redirect
    from app.services.service_orders import get_order_detail

    detail = get_order_detail(order_id)
    if not detail or not _order_visible(request, detail):
        return HTMLResponse("Ordem de serviço não encontrada.", status_code=404)
    values = _proposal_values(
        detail,
        {
            "razao_social": razao_social,
            "cnpj": cnpj,
            "endereco": endereco,
            "email": email,
            "responsavel": responsavel,
            "cargo": cargo,
            "telefone": telefone,
            "nome_fantasia": nome_fantasia,
            "colaboradores": colaboradores,
            "plan_key": plan_key,
            "valor_boleto": valor_boleto,
            "valor_cartao": valor_cartao,
            "valor_anual": valor_anual,
            "valor_mensal_equivalente": valor_mensal_equivalente,
            "valor_adicional": valor_adicional,
            "valor_final": valor_final,
            "observacao": observacao,
        },
    )
    if not normalize_text(values.get("razao_social")):
        return render(
            request,
            "activities/proposal_form.html",
            {
                "active_page": "activities",
                "order": detail,
                "values": values,
                "error": "Informe o nome do contratante.",
            },
            status_code=400,
        )
    typed_prices = (
        values.get("valor_boleto"),
        values.get("valor_cartao"),
        values.get("valor_anual"),
        values.get("valor_mensal_equivalente"),
        values.get("valor_final"),
    )
    if not any(any(ch.isdigit() for ch in normalize_text(item)) for item in typed_prices):
        return render(
            request,
            "activities/proposal_form.html",
            {
                "active_page": "activities",
                "order": detail,
                "values": values,
                "error": "Digite pelo menos um valor da negociação. O PDF não calcula preço sozinho.",
            },
            status_code=400,
        )
    try:
        pdf, filename = _proposal_pdf_bytes(detail, values)
    except Exception:
        return render(
            request,
            "activities/proposal_form.html",
            {
                "active_page": "activities",
                "order": detail,
                "values": values,
                "error": "Não consegui montar o PDF desta proposta.",
            },
            status_code=500,
        )
    mode = "inline" if normalize_text(disposicao) == "inline" else "attachment"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'{mode}; filename="{filename}"'},
    )


@router.get("/atividades/nova/modal", response_class=HTMLResponse)
async def activities_new_modal(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect
    return render(request, "partials/activities_new_modal.html", _modal_context(request))


@router.get("/atividades/{activity_id}/painel", response_class=HTMLResponse)
async def activities_detail_panel(request: Request, activity_id: str):
    redirect = require_auth(request)
    if redirect:
        return redirect

    df, columns = get_prepared_data()
    # Dataframe completo: o vínculo Abrir cadastro não pode depender do filtro de período
    panel = build_activity_detail_panel(DEFAULT_TENANT_ID, activity_id, df, columns)
    if not panel:
        return HTMLResponse("Atividade não encontrada.", status_code=404)

    return render(request, "partials/activities_detail_panel.html", panel)


@router.post("/atividades/{activity_id}/proxima-acao")
async def activities_update_next_action(
    request: Request,
    activity_id: str,
    next_action: str = Form(...),
):
    redirect = require_auth(request)
    if redirect:
        return JSONResponse({"error": "Não autenticado."}, status_code=401)

    user = normalize_text(request.session.get("username", "")) or "Usuário"
    normalized, error = atualizar_proxima_acao_atividade(DEFAULT_TENANT_ID, activity_id, next_action, user)
    if error:
        return JSONResponse({"error": error}, status_code=400)

    df, columns = get_prepared_data()
    timeline = build_activity_timeline_for_activity(DEFAULT_TENANT_ID, activity_id, df, columns)
    record = get_activity(DEFAULT_TENANT_ID, activity_id)
    stage = _serialize_activity(record, DEFAULT_TENANT_ID).get("stage", "") if record else ""
    return JSONResponse({
        "ok": True,
        "next_action": normalized,
        "timeline": timeline,
        "stage": stage,
    })


@router.post("/atividades/{activity_id}/mover-etapa", response_class=HTMLResponse)
async def activities_move_stage(
    request: Request,
    activity_id: str,
    stage_target: str = Form(...),
    lost_reason: str = Form(""),
    seller: str = Form("Todos os vendedores"),
    status: str = Form("Todos os status"),
    period_start: str = Form(""),
    period_end: str = Form(""),
    niche: str = Form("Todos os nichos"),
    state: str = Form("Todos os estados"),
    search: str = Form(""),
    tab: str = Form("todas"),
    activity_type: str = Form("Todos os tipos"),
    channel: str = Form("Todos os canais"),
    responsible: str = Form("Todos os responsáveis"),
    stage: str = Form("Todas as etapas"),
):
    redirect = require_auth(request)
    if redirect:
        return redirect

    user = normalize_text(request.session.get("username", "")) or "Usuário"
    _, error = mover_atividade_kanban(
        DEFAULT_TENANT_ID,
        activity_id,
        stage_target,
        user,
        lost_reason=lost_reason,
    )

    filters = parse_dashboard_filters(request, {
        "seller": seller,
        "status": status,
        "period_start": period_start,
        "period_end": period_end,
        "niche": niche,
        "state": state,
        "search": search,
    })
    activities_params = _parse_activities_params(request, {
        "tab": tab,
        "activity_type": activity_type,
        "channel": channel,
        "responsible": responsible,
        "stage": stage,
    })

    if error:
        return _render_content(request, filters, activities_params, error=error)
    return _render_content(
        request,
        filters,
        activities_params,
        success=f"Atividade movida para {stage_target}.",
    )


@router.get("/atividades/api/leads")
async def activities_search_leads(request: Request, q: str = "", sheet_row: int = 0):
    redirect = require_auth(request)
    if redirect:
        return redirect
    df, columns = get_prepared_data()
    options = get_filter_options(df)
    filters = apply_default_period_filters(parse_dashboard_filters(request), df)
    filters = apply_seller_scope(request, filters, options["seller_options"], is_admin(request))
    scoped_df = scope_df_for_lead_search(df, filters.seller)
    current_user = normalize_text(request.session.get("username", ""))
    query = normalize_text(q)
    try:
        row_id = int(sheet_row or 0)
    except (TypeError, ValueError):
        row_id = 0
    leads = buscar_leads_para_atividade(
        scoped_df,
        columns,
        DEFAULT_TENANT_ID,
        query,
        current_user=current_user,
        is_admin_user=is_admin(request),
        limit=100 if not query else 20,
        sheet_row=row_id or None,
    )
    return JSONResponse({"items": leads})


@router.get("/atividades/api/acoes")
async def activities_stage_actions(request: Request, stage: str = "Novo Lead"):
    redirect = require_auth(request)
    if redirect:
        return redirect
    return JSONResponse({"items": buscar_acoes_por_etapa(stage)})


@router.get("/atividades/api/sugerir-resultado")
async def activities_suggest_result(request: Request, result: str = "", stage: str = ""):
    redirect = require_auth(request)
    if redirect:
        return redirect
    return JSONResponse(sugerir_fluxo_por_resultado(result, stage))


@router.post("/atividades/nova", response_class=HTMLResponse)
async def activities_create(
    request: Request,
    sheet_row: int = Form(0),
    empresa: str = Form(""),
    contato: str = Form(""),
    stage: str = Form(""),
    activity_type: str = Form(""),
    process_action: str = Form(""),
    channel: str = Form("WhatsApp"),
    channel_other: str = Form(""),
    assigned_user_id: str = Form(""),
    scheduled_date: str = Form(""),
    scheduled_time: str = Form("09:00"),
    status: str = Form("pendente", alias="activity_status"),
    priority: str = Form("Média"),
    description: str = Form(""),
    result: str = Form(""),
    note: str = Form(""),
    next_action: str = Form(""),
    next_action_date: str = Form(""),
    next_action_time: str = Form("10:00"),
    next_action_channel: str = Form(""),
    next_action_assigned: str = Form(""),
    move_stage: str = Form(""),
    move_stage_confirm: str = Form(""),
    lost_reason: str = Form(""),
    close_value: str = Form(""),
    close_payment: str = Form(""),
    tab: str = Form("todas"),
    page: int = Form(1),
    per_page: int = Form(10),
    seller: str = Form("Todos os vendedores"),
    status_filter: str = Form("Todos os status"),
    period_start: str = Form(""),
    period_end: str = Form(""),
    niche: str = Form("Todos os nichos"),
    state: str = Form("Todos os estados"),
    search: str = Form(""),
    return_to: str = Form(""),
):
    redirect = require_auth(request)
    if redirect:
        return redirect

    user = normalize_text(request.session.get("username", "")) or "Usuário"
    admin_user = is_admin(request)
    safe_return = normalize_text(return_to)
    if safe_return and not safe_return.startswith("/"):
        safe_return = ""

    df, columns = get_prepared_data()
    if sheet_row:
        row_match = df[df["_sheet_row"] == sheet_row]
        if row_match.empty:
            response = render(
                request,
                "partials/activities_new_modal.html",
                _modal_context(
                    request,
                    "Lead não encontrado.",
                    return_to=safe_return,
                    prefill_sheet_row=sheet_row,
                    prefill_empresa=empresa,
                ),
            )
            response.status_code = 422
            response.headers["HX-Retarget"] = "#activity-modal-root"
            response.headers["HX-Reswap"] = "innerHTML"
            return response
        row = row_match.iloc[0]
        # Nome sempre da planilha do sheet_row — evita gravar empresa trocada
        from app.services.activity_service import _contact_name, _lead_is_accessible
        if not _lead_is_accessible(row, user, admin_user):
            response = render(
                request,
                "partials/activities_new_modal.html",
                _modal_context(
                    request,
                    "Sem permissão para este lead.",
                    return_to=safe_return,
                    prefill_sheet_row=sheet_row,
                    prefill_empresa=empresa,
                ),
            )
            response.status_code = 403
            response.headers["HX-Retarget"] = "#activity-modal-root"
            response.headers["HX-Reswap"] = "innerHTML"
            return response
        empresa = normalize_text(row.get("_empresa", "")) or empresa
        contato = _contact_name(row, columns) or contato

    payload = {
        "sheet_row": sheet_row,
        "empresa": empresa,
        "contato": contato,
        "stage": stage,
        "activity_type": activity_type,
        "process_action": process_action,
        "channel": channel,
        "channel_other": channel_other,
        "assigned_user_id": assigned_user_id,
        "scheduled_date": scheduled_date,
        "scheduled_time": scheduled_time,
        "status": status,
        "priority": priority,
        "description": description,
        "result": result,
        "note": note,
        "next_action": next_action,
        "next_action_date": next_action_date,
        "next_action_time": next_action_time,
        "next_action_channel": next_action_channel,
        "next_action_assigned": next_action_assigned,
        "move_stage": move_stage,
        "move_stage_confirm": move_stage_confirm,
        "lost_reason": lost_reason,
        "close_value": close_value,
        "close_payment": close_payment,
    }

    _, error = criar_atividade(DEFAULT_TENANT_ID, payload, user, is_admin_user=admin_user)
    if error:
        response = render(
            request,
            "partials/activities_new_modal.html",
            _modal_context(
                request,
                error,
                return_to=safe_return,
                prefill_sheet_row=sheet_row,
                prefill_empresa=empresa,
            ),
        )
        response.status_code = 422
        response.headers["HX-Retarget"] = "#activity-modal-root"
        response.headers["HX-Reswap"] = "innerHTML"
        return response

    if safe_return:
        response = HTMLResponse("")
        response.headers["HX-Redirect"] = safe_return
        return response

    filters = parse_dashboard_filters(request, {
        "seller": seller,
        "status": status_filter,
        "period_start": period_start,
        "period_end": period_end,
        "niche": niche,
        "state": state,
        "search": search,
    })
    activities_params = _parse_activities_params(request, {
        "tab": tab,
        "page": page,
        "per_page": per_page,
    })
    response = _render_content(request, filters, activities_params, success="Atividade criada com sucesso.")
    response.headers["HX-Trigger"] = json.dumps({"activityModalClose": True})
    return response


@router.post("/atividades/filtros", response_class=HTMLResponse)
async def activities_filters(
    request: Request,
    seller: str = Form("Todos os vendedores"),
    status: str = Form("Todos os status"),
    period_start: str = Form(""),
    period_end: str = Form(""),
    niche: str = Form("Todos os nichos"),
    state: str = Form("Todos os estados"),
    search: str = Form(""),
    tab: str = Form("todas"),
    activity_type: str = Form("Todos os tipos"),
    channel: str = Form("Todos os canais"),
    responsible: str = Form("Todos os responsáveis"),
    stage: str = Form("Todas as etapas"),
    page: int = Form(1),
    per_page: int = Form(10),
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
    activities_params = _parse_activities_params(request, {
        "tab": tab,
        "activity_type": activity_type,
        "channel": channel,
        "responsible": responsible,
        "stage": stage,
        "page": page,
        "per_page": per_page,
    })
    return _render_content(request, filters, activities_params)


@router.post("/atividades/{activity_id}/salvar", response_class=HTMLResponse)
async def activities_save_inline(
    request: Request,
    activity_id: str,
    status: str = Form("pendente"),
    result: str = Form(""),
    next_action: str = Form(""),
    next_action_date: str = Form(""),
    next_action_time: str = Form("09:00"),
    next_action_channel: str = Form("WhatsApp"),
    channel: str = Form("WhatsApp"),
    assigned_user_id: str = Form(""),
    note: str = Form(""),
    result_notes: str = Form(""),
    move_stage: str = Form(""),
    lost_reason: str = Form(""),
    scheduled_date: str = Form(""),
    scheduled_time: str = Form(""),
    tab: str = Form("todas"),
    activity_type: str = Form("Todos os tipos"),
    channel_filter: str = Form("Todos os canais"),
    responsible: str = Form("Todos os responsáveis"),
    stage_filter: str = Form("Todas as etapas"),
    page: int = Form(1),
    per_page: int = Form(10),
    seller: str = Form("Todos os vendedores"),
    status_filter: str = Form("Todos os status"),
    period_start: str = Form(""),
    period_end: str = Form(""),
    niche: str = Form("Todos os nichos"),
    state: str = Form("Todos os estados"),
    search: str = Form(""),
):
    redirect = require_auth(request)
    if redirect:
        return redirect

    user = normalize_text(request.session.get("username", "")) or "Usuário"
    _, error = atualizar_atividade_inline(
        DEFAULT_TENANT_ID,
        activity_id,
        {
            "status": status,
            "result": result,
            "next_action": next_action,
            "next_action_date": next_action_date,
            "next_action_time": next_action_time,
            "next_action_channel": next_action_channel,
            "channel": channel,
            "assigned_user_id": assigned_user_id,
            "note": note,
            "result_notes": result_notes,
            "move_stage": move_stage,
            "lost_reason": lost_reason,
            "scheduled_date": scheduled_date,
            "scheduled_time": scheduled_time,
        },
        user,
    )

    filters = parse_dashboard_filters(request, {
        "seller": seller,
        "status": status_filter,
        "period_start": period_start,
        "period_end": period_end,
        "niche": niche,
        "state": state,
        "search": search,
    })
    activities_params = _parse_activities_params(request, {
        "tab": tab,
        "activity_type": activity_type,
        "channel": channel_filter,
        "responsible": responsible,
        "stage": stage_filter,
        "page": page,
        "per_page": per_page,
    })

    if error:
        return _render_content(request, filters, activities_params, error=error)
    return _render_content(request, filters, activities_params, success="Atividade atualizada com sucesso.")


@router.post("/atividades/{activity_id}/cancelar", response_class=HTMLResponse)
async def activities_cancel(request: Request, activity_id: str, reason: str = Form("")):
    redirect = require_auth(request)
    if redirect:
        return redirect
    user = normalize_text(request.session.get("username", "")) or "Usuário"
    cancelar_atividade(DEFAULT_TENANT_ID, activity_id, user, reason)
    filters = parse_dashboard_filters(request)
    return _render_content(request, filters, _parse_activities_params(request), success="Atividade cancelada.")


@router.post("/atividades/{activity_id}/excluir", response_class=HTMLResponse)
async def activities_delete(request: Request, activity_id: str):
    redirect = require_auth(request)
    if redirect:
        return redirect
    if not is_admin(request):
        filters = parse_dashboard_filters(request)
        return _render_content(request, filters, _parse_activities_params(request), error="Sem permissão para excluir.")
    soft_delete_activity(DEFAULT_TENANT_ID, activity_id)
    filters = parse_dashboard_filters(request)
    return _render_content(request, filters, _parse_activities_params(request), success="Atividade excluída.")


@router.post("/atividades/atualizar")
async def activities_refresh(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect
    invalidate_sheet_cache()
    from app.services.sheet_crm_storage import ensure_crm_storage_tabs
    from app.services.activities_storage import invalidate_activities_cache, reload_activities_store
    from app.services.lead_actions_storage import invalidate_lead_actions_cache, reload_lead_actions_store

    ensure_crm_storage_tabs()
    invalidate_activities_cache()
    invalidate_lead_actions_cache()
    reload_activities_store(force_refresh=True)
    reload_lead_actions_store(force_refresh=True)
    return RedirectResponse(url="/atividades", status_code=303)
