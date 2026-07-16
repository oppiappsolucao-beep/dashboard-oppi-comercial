from datetime import date

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from app.dependencies import get_prepared_data, get_pricing_store, require_auth
from app.templating import render
from app.services.filters import DashboardFilters, apply_dashboard_filters, apply_default_period_filters
from app.services.filters import get_filter_options as get_dashboard_filter_options
from app.services.commercial_services import get_commercial_service_options
from app.services.legacy_core import (
    DuplicateRegistrationError,
    STATUS_OPTIONS,
    _diagnostic_get_answers,
    build_client_commercial_summary,
    get_colaborador_options,
    invalidate_sheet_cache,
    normalize_search_text,
    normalize_text,
    resolve_company_status,
    safe_series,
    status_badge_class,
    status_group,
    update_company_status_in_sheet,
)
from app.services.lead_actions_storage import DEFAULT_TENANT_ID, get_lead_action
from app.services.overview import build_client_action
from app.services.registration import get_seller_options, save_company_edit

router = APIRouter()


def _contract_value(row, columns, key):
    column_name = columns.get(key)
    if column_name and column_name in row.index:
        value = normalize_text(row.get(column_name, ""))
        if value:
            return value
    return "Não informado"


def _contract_edit_value(row, columns, key):
    column_name = columns.get(key)
    if column_name and column_name in row.index:
        return normalize_text(row.get(column_name, ""))
    return ""


def _get_row_by_sheet(df, sheet_row: int):
    matches = df[df["_sheet_row"] == int(sheet_row)]
    if matches.empty:
        return None
    return matches.iloc[0]


@router.get("/cadastro/todos", response_class=HTMLResponse)
async def contracts_list(request: Request, order: str = "recentes"):
    redirect = require_auth(request)
    if redirect:
        return redirect

    refresh = request.query_params.get("refresh") == "1"
    df, columns = get_prepared_data(refresh=refresh)
    options = get_dashboard_filter_options(df)

    filters = apply_default_period_filters(
        DashboardFilters(
            seller=request.query_params.get("seller", "Todos os vendedores"),
            status=request.query_params.get("status", "Todos os status"),
            period_start=date.fromisoformat(request.query_params["period_start"])
            if request.query_params.get("period_start") else None,
            period_end=date.fromisoformat(request.query_params["period_end"])
            if request.query_params.get("period_end") else None,
            niche=request.query_params.get("niche", "Todos os nichos"),
            state=request.query_params.get("state", "Todos os estados"),
            search=request.query_params.get("search", ""),
        ),
        df,
    )

    filtered_df = apply_dashboard_filters(df, columns, filters)

    if normalize_text(filters.search):
        term = normalize_search_text(filters.search)
        filtered_df = filtered_df[
            filtered_df["_empresa"].apply(lambda v: term in normalize_search_text(v))
        ].copy()

    names_df = filtered_df[["_empresa", "_sheet_row"]].copy()
    names_df["Empresa"] = names_df["_empresa"].apply(normalize_text)
    names_df = names_df[names_df["Empresa"] != ""].copy()

    sort_mode = "alfabetica" if order == "alfabetica" else "recentes"
    if sort_mode == "alfabetica":
        names_df = names_df.sort_values("Empresa", key=lambda s: s.map(normalize_search_text))
    else:
        names_df = names_df.sort_values("_sheet_row", ascending=False)

    companies = []
    for _, row in names_df.iterrows():
        full_row = _get_row_by_sheet(filtered_df, int(row["_sheet_row"]))
        if full_row is None:
            full_row = _get_row_by_sheet(df, int(row["_sheet_row"]))
        status = "Novo Lead"
        vendedor = "Sem vendedor"
        nicho = "—"
        estado = "—"
        if full_row is not None:
            status = resolve_company_status(full_row)
            vendedor = normalize_text(full_row.get("_vendedor", "")) or "Sem vendedor"
            nicho = normalize_text(full_row.get("_nicho", "")) or "—"
            estado = normalize_text(full_row.get("_estado", "")) or "—"

        empresa = row["Empresa"]
        initials = "".join(part[0] for part in empresa.split()[:2]).upper() if empresa else "—"
        companies.append({
            "name": empresa,
            "sheet_row": int(row["_sheet_row"]),
            "initials": initials[:2],
            "vendedor": vendedor,
            "status": status,
            "status_class": status_badge_class(status),
            "nicho": nicho,
            "estado": estado,
        })

    return render(
        request,
        "contracts/list.html",
        {
            "active_page": "contracts",
            "companies": companies,
            "count": len(companies),
            "options": options,
            "filters": filters,
            "sort_mode": sort_mode,
            "next_order": "alfabetica" if sort_mode == "recentes" else "recentes",
            "status_options": STATUS_OPTIONS,
        },
    )


@router.get("/cadastro/todos/{sheet_row}", response_class=HTMLResponse)
async def contract_detail(request: Request, sheet_row: int):
    redirect = require_auth(request)
    if redirect:
        return redirect

    get_pricing_store(request)
    df, columns = get_prepared_data()
    row = _get_row_by_sheet(df, sheet_row)
    if row is None:
        return RedirectResponse(url="/cadastro/todos", status_code=303)

    company_name = normalize_text(row.get("_empresa", ""))
    pricing_answers = _diagnostic_get_answers().get(company_name)
    commercial = build_client_commercial_summary(row, columns, pricing_answers)

    fields = {
        "empresa": _contract_value(row, columns, "empresa"),
        "data_abertura": _contract_value(row, columns, "data_abertura"),
        "capital": _contract_value(row, columns, "capital"),
        "cnpj": _contract_value(row, columns, "cnpj"),
        "endereco": _contract_value(row, columns, "endereco"),
        "email": _contract_value(row, columns, "email"),
        "site": _contract_value(row, columns, "site"),
        "telefone_b2b": _contract_value(row, columns, "telefone_b2b"),
        "telefone_fixo": _contract_value(row, columns, "telefone_fixo"),
        "telefone_alternativo": _contract_value(row, columns, "telefone_alternativo"),
        "socio_1": _contract_value(row, columns, "socio_1"),
        "cpf_socio_1": _contract_value(row, columns, "cpf_socio_1"),
        "email_socio_1": _contract_value(row, columns, "email_socio_1"),
        "telefone_socio_1": _contract_value(row, columns, "telefone_socio_1"),
        "socio_2": _contract_value(row, columns, "socio_2"),
        "telefone_socio_2": _contract_value(row, columns, "telefone_socio_2"),
        "cpf_socio_2": _contract_value(row, columns, "cpf_socio_2"),
        "socio_3": _contract_value(row, columns, "socio_3"),
        "telefone_socio_3": _contract_value(row, columns, "telefone_socio_3"),
        "cpf_socio_3": _contract_value(row, columns, "cpf_socio_3"),
        "instagram": _contract_value(row, columns, "instagram"),
        "linkedin": _contract_value(row, columns, "linkedin"),
        "vendedor": normalize_text(row.get("_vendedor", "")) or "Sem vendedor",
        "status": status_group(row.get("_status_original", row.get("_status_grupo", "Novo Lead"))),
        "data_chamado": _contract_value(row, columns, "data_chamado"),
        "observacoes": _contract_value(row, columns, "observacoes"),
        "nicho": normalize_text(row.get("_nicho", "")),
        "estado": normalize_text(row.get("_estado", "")),
        "pontuacao": int(row.get("_pontuacao", 0)),
        "classificacao": normalize_text(row.get("_classificacao", "")),
    }

    lead_action = get_lead_action(DEFAULT_TENANT_ID, sheet_row) or {}
    interactions = lead_action.get("interactions") if isinstance(lead_action.get("interactions"), list) else []
    interactions = list(reversed(interactions))

    return render(
        request,
        "contracts/detail.html",
        {
            "active_page": "contracts",
            "sheet_row": sheet_row,
            "fields": fields,
            "commercial": commercial,
            "client_action": build_client_action(row, columns),
            "lead_action": lead_action,
            "interactions": interactions,
        },
    )


@router.get("/cadastro/todos/{sheet_row}/editar", response_class=HTMLResponse)
async def contract_edit_page(request: Request, sheet_row: int):
    redirect = require_auth(request)
    if redirect:
        return redirect

    df, columns = get_prepared_data()
    row = _get_row_by_sheet(df, sheet_row)
    if row is None:
        return RedirectResponse(url="/cadastro/todos", status_code=303)

    current_status = status_group(row.get("_status_original", row.get("_status_grupo", "Novo Lead")))
    if current_status not in STATUS_OPTIONS:
        current_status = "Novo Lead"

    data_chamado = _contract_edit_value(row, columns, "data_chamado")
    try:
        parsed_date = date.fromisoformat(data_chamado) if "-" in data_chamado else date.today()
    except ValueError:
        parsed_date = date.today()

    return render(
        request,
        "contracts/edit.html",
        {
            "active_page": "contracts",
            "sheet_row": sheet_row,
            "seller_options": get_seller_options(df),
            "status_options": STATUS_OPTIONS,
            "current_status": current_status,
            "data_chamado": parsed_date.isoformat(),
            "values": {key: _contract_edit_value(row, columns, key) for key in [
                "empresa", "data_abertura", "capital", "cnpj", "endereco", "email", "site",
                "telefone_b2b", "telefone_fixo", "telefone_alternativo",
                "socio_1", "cpf_socio_1", "email_socio_1", "telefone_socio_1",
                "socio_2", "telefone_socio_2", "cpf_socio_2",
                "socio_3", "telefone_socio_3", "cpf_socio_3",
                "instagram", "linkedin", "observacoes",
                "servico", "valor_proposta", "colaboradores",
            ]},
            "service_options": get_commercial_service_options(),
            "colaborador_options": get_colaborador_options(),
            "vendedor": normalize_text(row.get("_vendedor", "")) or "Sem vendedor",
            "error": request.session.pop("edit_error", ""),
        },
    )


@router.post("/cadastro/todos/{sheet_row}/editar")
async def contract_edit_submit(request: Request, sheet_row: int):
    redirect = require_auth(request)
    if redirect:
        return redirect

    form = await request.form()
    if form.get("action") == "cancel":
        return RedirectResponse(url=f"/cadastro/todos/{sheet_row}", status_code=303)

    form_dict = dict(form)
    form_dict["email_empresa"] = form_dict.pop("email", form_dict.get("email_empresa", ""))

    try:
        save_company_edit(sheet_row, form_dict)
        return RedirectResponse(url=f"/cadastro/todos/{sheet_row}", status_code=303)
    except DuplicateRegistrationError as error:
        request.session["edit_error"] = str(error)
    except ValueError as error:
        request.session["edit_error"] = str(error)
    except Exception as error:
        request.session["edit_error"] = f"Não consegui salvar: {error}"

    return RedirectResponse(url=f"/cadastro/todos/{sheet_row}/editar", status_code=303)


@router.post("/cadastro/todos/{sheet_row}/status", response_class=HTMLResponse)
async def contract_update_status(request: Request, sheet_row: int, status: str = Form(...)):
    redirect = require_auth(request)
    if redirect:
        return redirect

    df, columns = get_prepared_data()
    row = _get_row_by_sheet(df, sheet_row)
    if row is None:
        return RedirectResponse(url="/cadastro/todos", status_code=303)

    new_status = normalize_text(status)
    if new_status not in STATUS_OPTIONS:
        new_status = "Novo Lead"

    try:
        update_company_status_in_sheet(sheet_row, new_status, columns)
    except Exception as error:
        if request.headers.get("HX-Request"):
            return HTMLResponse(
                f'<span class="contracts-status-error">Erro: {error}</span>',
                status_code=500,
            )
        request.session["contracts_status_error"] = str(error)
        return RedirectResponse(url=request.headers.get("referer", "/cadastro/todos"), status_code=303)

    status_context = {
        "sheet_row": sheet_row,
        "status": new_status,
        "status_class": status_badge_class(new_status),
        "status_options": STATUS_OPTIONS,
    }

    if request.headers.get("HX-Request"):
        return render(request, "partials/contracts_status_cell.html", status_context)

    return RedirectResponse(url=request.headers.get("referer", "/cadastro/todos"), status_code=303)


@router.post("/cadastro/todos/atualizar")
async def contracts_refresh(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect
    invalidate_sheet_cache()
    return RedirectResponse(url="/cadastro/todos?refresh=1", status_code=303)
