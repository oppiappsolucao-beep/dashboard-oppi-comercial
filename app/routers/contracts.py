from datetime import date

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from app.dependencies import get_prepared_data, get_pricing_store, require_auth
from app.templating import render
from app.services.filters import DashboardFilters, apply_dashboard_filters, apply_default_period_filters
from app.services.filters import get_filter_options as get_dashboard_filter_options
from app.services.commercial_services import get_commercial_service_options
from app.services.closed_services import (
    PAYMENT_METHOD_OPTIONS,
    closed_services_has_data,
    load_closed_services,
    parse_closed_services_from_form,
    save_closed_services,
)
from app.services.payment_history import (
    PAYMENT_STATUS_OPTIONS,
    financial_summary,
    load_payment_history,
    parse_payment_history_from_form,
    save_payment_history,
)
from app.services.legacy_core import (
    DuplicateRegistrationError,
    STATUS_OPTIONS,
    get_colaborador_options,
    invalidate_sheet_cache,
    normalize_search_text,
    normalize_text,
    resolve_address_form_values,
    resolve_company_status,
    safe_series,
    status_badge_class,
    status_group,
    update_company_status_in_sheet,
)
from app.services.lead_actions_storage import DEFAULT_TENANT_ID
from app.services.activity_service import build_cadastro_activities_context
from app.services.registration import (
    CADASTRO_TIPO_OPTIONS,
    build_cadastro_edit_page_context,
    get_seller_options,
    infer_partners_count,
    load_access_fields,
    resolve_cadastro_tipo,
    save_access_fields,
    save_cadastro_tipo,
    save_company_edit,
    delete_company_registration,
)

router = APIRouter()


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


def _resolve_edit_from_page(value: str) -> str:
    normalized = normalize_text(value)
    return normalized if normalized in {"leads", "activities"} else ""


def _edit_page_url(sheet_row: int, *, tab: str = "", from_page: str = "") -> str:
    params: list[str] = []
    if tab:
        params.append(f"tab={tab}")
    if from_page:
        params.append(f"from={from_page}")
    query = f"?{'&'.join(params)}" if params else ""
    return f"/cadastro/todos/{sheet_row}/editar{query}"


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

    from_page = _resolve_edit_from_page(request.query_params.get("from"))
    tab = normalize_text(request.query_params.get("tab"))
    return RedirectResponse(
        url=_edit_page_url(sheet_row, tab=tab, from_page=from_page),
        status_code=303,
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

    data_chamado_raw = _contract_edit_value(row, columns, "data_chamado")
    try:
        parsed_date = date.fromisoformat(data_chamado_raw) if "-" in data_chamado_raw else date.today()
    except ValueError:
        parsed_date = date.today()

    values = {key: _contract_edit_value(row, columns, key) for key in [
                "empresa", "data_abertura", "capital", "cnpj", "endereco", "endereco_numero", "endereco_complemento",
                "cep", "bairro", "municipio", "uf", "email", "site",
                "telefone_b2b", "telefone_fixo", "telefone_alternativo",
                "socio_1", "cpf_socio_1", "email_socio_1", "telefone_socio_1",
                "socio_2", "telefone_socio_2", "cpf_socio_2",
                "socio_3", "telefone_socio_3", "cpf_socio_3",
                "instagram", "linkedin", "observacoes",
                "servico", "valor_proposta", "colaboradores",
            ]}
    values.update(resolve_address_form_values(row, columns))
    values.update(load_access_fields(DEFAULT_TENANT_ID, sheet_row))

    cadastro_tipo = resolve_cadastro_tipo(DEFAULT_TENANT_ID, sheet_row, cnpj=values.get("cnpj", ""))
    from_page = _resolve_edit_from_page(request.query_params.get("from"))
    active_tab = normalize_text(request.query_params.get("tab")) or "dados"
    tab_aliases = {
        "cadastro": "dados",
        "dados": "dados",
        "atividades": "atividades",
        "proposta": "proposta",
        "propostas": "proposta",
        "financeiro": "financeiro",
        "suporte": "suporte",
    }
    active_tab = tab_aliases.get(active_tab, "dados")
    activities_ctx = build_cadastro_activities_context(
        DEFAULT_TENANT_ID,
        sheet_row,
        values.get("empresa", ""),
    )
    page_ctx = build_cadastro_edit_page_context(
        tenant_id=DEFAULT_TENANT_ID,
        sheet_row=sheet_row,
        row=row,
        columns=columns,
        values=values,
        vendedor=normalize_text(row.get("_vendedor", "")) or "Sem vendedor",
        current_status=current_status,
        data_chamado=data_chamado_raw or parsed_date.isoformat(),
        cadastro_tipo=cadastro_tipo,
        activities=activities_ctx.get("activities", []),
        interactions=activities_ctx.get("interactions", []),
    )
    closed_services = load_closed_services(
        DEFAULT_TENANT_ID,
        sheet_row,
        servico=values.get("servico", ""),
        valor_proposta=values.get("valor_proposta", ""),
    )
    payment_history = load_payment_history(DEFAULT_TENANT_ID, sheet_row)
    if closed_services:
        page_ctx["proposals_count"] = len([
            item for item in closed_services
            if any(normalize_text(item.get(key)) for key in ("servico", "valor", "vencimento"))
        ])

    back_href = {
        "leads": "/leads-e-empresas",
        "activities": "/atividades",
    }.get(from_page, "/cadastro/todos")
    active_sidebar = {
        "leads": "leads",
        "activities": "activities",
    }.get(from_page, "contracts")

    return render(
        request,
        "contracts/edit.html",
        {
            "active_page": active_sidebar,
            "from_page": from_page,
            "back_href": back_href,
            "back_label": {
                "leads": "Empresas",
                "activities": "Atividades",
            }.get(from_page, "Todos os cadastros"),
            "sheet_row": sheet_row,
            "seller_options": get_seller_options(df),
            "status_options": STATUS_OPTIONS,
            "current_status": current_status,
            "data_chamado": parsed_date.isoformat(),
            "values": values,
            "partners_count": infer_partners_count(values),
            "service_options": get_commercial_service_options(),
            "payment_method_options": PAYMENT_METHOD_OPTIONS,
            "payment_status_options": PAYMENT_STATUS_OPTIONS,
            "closed_services": closed_services,
            "payment_history": payment_history,
            "financial_summary": financial_summary(closed_services, payment_history),
            "colaborador_options": get_colaborador_options(),
            "vendedor": normalize_text(row.get("_vendedor", "")) or "Sem vendedor",
            "error": request.session.pop("edit_error", ""),
            "success": request.session.pop("edit_success", ""),
            "cadastro_tipo": cadastro_tipo,
            "cadastro_tipo_options": CADASTRO_TIPO_OPTIONS,
            "active_tab": active_tab,
            **activities_ctx,
            **page_ctx,
        },
    )


@router.post("/cadastro/todos/{sheet_row}/editar")
async def contract_edit_submit(request: Request, sheet_row: int):
    redirect = require_auth(request)
    if redirect:
        return redirect

    form = await request.form()
    from_page = _resolve_edit_from_page(form.get("from"))
    if form.get("action") == "cancel":
        if from_page == "leads":
            return RedirectResponse(url="/leads-e-empresas", status_code=303)
        if from_page == "activities":
            return RedirectResponse(url="/atividades", status_code=303)
        return RedirectResponse(url=_edit_page_url(sheet_row, from_page=from_page), status_code=303)

    form_dict = dict(form)
    form_dict["email_empresa"] = form_dict.pop("email", form_dict.get("email_empresa", ""))
    action = normalize_text(form.get("action"))

    try:
        if action == "save_financeiro":
            closed_items = parse_closed_services_from_form(form)
            save_closed_services(DEFAULT_TENANT_ID, sheet_row, closed_items)
            payments = parse_payment_history_from_form(form)
            save_payment_history(DEFAULT_TENANT_ID, sheet_row, payments)
            request.session["edit_success"] = "Financeiro atualizado com sucesso."
            return RedirectResponse(
                url=_edit_page_url(sheet_row, tab="financeiro", from_page=from_page),
                status_code=303,
            )

        closed_items = parse_closed_services_from_form(form)
        if closed_services_has_data(closed_items):
            primary_closed = save_closed_services(DEFAULT_TENANT_ID, sheet_row, closed_items)
            form_dict["servico"] = primary_closed.get("servico", "")
            form_dict["valor_proposta"] = primary_closed.get("valor", "")
        save_company_edit(sheet_row, form_dict)
        save_cadastro_tipo(DEFAULT_TENANT_ID, sheet_row, form_dict.get("cadastro_tipo", "lead"))
        save_access_fields(DEFAULT_TENANT_ID, sheet_row, form_dict)
        invalidate_sheet_cache()
        request.session["edit_success"] = "Cadastro salvo com sucesso."
        return RedirectResponse(url=_edit_page_url(sheet_row, from_page=from_page), status_code=303)
    except DuplicateRegistrationError as error:
        request.session["edit_error"] = str(error)
    except ValueError as error:
        request.session["edit_error"] = str(error)
    except Exception as error:
        request.session["edit_error"] = f"Não consegui salvar: {error}"

    return RedirectResponse(url=_edit_page_url(sheet_row, from_page=from_page), status_code=303)


@router.post("/cadastro/todos/{sheet_row}/excluir")
async def contract_delete(
    request: Request,
    sheet_row: int,
    confirm_text: str = Form(""),
    from_: str = Form("", alias="from"),
):
    redirect = require_auth(request)
    if redirect:
        return redirect

    from_page = _resolve_edit_from_page(from_)
    edit_url = _edit_page_url(sheet_row, from_page=from_page)

    if normalize_text(confirm_text).lower() != "excluir":
        request.session["edit_error"] = "Digite excluir para confirmar que deseja realmente excluir."
        return RedirectResponse(url=edit_url, status_code=303)

    df, _columns = get_prepared_data()
    row = _get_row_by_sheet(df, sheet_row)
    if row is None:
        request.session["edit_error"] = "Cadastro não encontrado."
        return RedirectResponse(url="/cadastro/todos", status_code=303)

    try:
        delete_company_registration(DEFAULT_TENANT_ID, sheet_row)
    except ValueError as error:
        request.session["edit_error"] = str(error)
        return RedirectResponse(url=edit_url, status_code=303)
    except Exception as error:
        request.session["edit_error"] = f"Não consegui excluir o cadastro: {error}"
        return RedirectResponse(url=edit_url, status_code=303)

    return RedirectResponse(url={
        "leads": "/leads-e-empresas",
        "activities": "/atividades",
    }.get(from_page, "/cadastro/todos"), status_code=303)


@router.post("/cadastro/todos/{sheet_row}/tipo")
async def contract_update_tipo(request: Request, sheet_row: int, cadastro_tipo: str = Form(...)):
    redirect = require_auth(request)
    if redirect:
        return redirect

    save_cadastro_tipo(DEFAULT_TENANT_ID, sheet_row, cadastro_tipo)
    referer = request.headers.get("referer") or f"/cadastro/todos/{sheet_row}/editar"
    return RedirectResponse(url=referer, status_code=303)


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
