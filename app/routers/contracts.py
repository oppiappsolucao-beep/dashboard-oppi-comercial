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
from app.services.cadastro_billing import (
    BILLING_FORM_OPTIONS,
    PLAN_CYCLE_OPTIONS,
    asaas_history_for_cadastro,
    generate_asaas_invoice,
    load_billing_plan,
    parse_billing_plan_from_form,
    save_billing_plan,
)
from app.services.asaas_client import AsaasError, is_configured as asaas_is_configured
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
    get_niche_options,
    get_seller_options,
    infer_partners_count,
    is_cadastro_ativo,
    load_access_fields,
    resolve_cadastro_tipo,
    resolve_nicho,
    save_access_fields,
    save_cadastro_ativo,
    save_cadastro_tipo,
    save_company_edit,
    save_nicho,
    save_setor,
    delete_company_registration,
)

router = APIRouter()

# Lista misturada /cadastro/todos foi descontinuada — Empresas aprovada.
_CADASTRO_LIST_URL = "/leads-e-empresas"


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
    normalized = normalize_text(value).lower()
    if normalized in {"leads", "activities", "funnel", "attendances", "overview", "contracts"}:
        return normalized
    return ""


def _list_url_for_from_page(from_page: str = "") -> str:
    return {
        "leads": "/leads-e-empresas",
        "activities": "/atividades",
        "funnel": "/funil-de-vendas",
        "attendances": "/atendimentos",
        "overview": "/",
    }.get(normalize_text(from_page).lower(), _CADASTRO_LIST_URL)


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
    """Tela misturada descontinuada — redireciona para Empresas."""
    redirect = require_auth(request)
    if redirect:
        return redirect
    return RedirectResponse(url=_CADASTRO_LIST_URL, status_code=303)


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
        return RedirectResponse(url=_CADASTRO_LIST_URL, status_code=303)

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
                "telefone_b2b", "nome_contato", "telefone_fixo", "telefone_alternativo",
                "socio_1", "cpf_socio_1", "email_socio_1", "telefone_socio_1",
                "socio_2", "telefone_socio_2", "cpf_socio_2",
                "socio_3", "telefone_socio_3", "cpf_socio_3",
                "instagram", "linkedin", "observacoes",
                "servico", "valor_proposta", "colaboradores",
            ]}
    values.update(resolve_address_form_values(row, columns))
    values.update(load_access_fields(DEFAULT_TENANT_ID, sheet_row))
    values["nicho"] = resolve_nicho(
        DEFAULT_TENANT_ID,
        sheet_row,
        empresa=values.get("empresa", ""),
        fallback=normalize_text(row.get("_nicho", "")),
    )
    values["is_filial"] = False
    values["empresa_matriz_sheet_row"] = ""
    values["empresa_matriz_nome"] = ""
    try:
        from app.services.crm_registrations_storage import (
            get_registration_by_sheet_row,
            is_crm_postgres_ready,
            registration_to_payload,
        )

        if is_crm_postgres_ready():
            pg_row = get_registration_by_sheet_row(int(sheet_row))
            if pg_row is not None:
                pg = registration_to_payload(pg_row)
                values["is_filial"] = bool(pg.get("is_filial"))
                matriz_row = pg.get("empresa_matriz_sheet_row")
                if matriz_row:
                    values["empresa_matriz_sheet_row"] = str(int(matriz_row))
                    matriz = get_registration_by_sheet_row(int(matriz_row))
                    if matriz is not None:
                        values["empresa_matriz_nome"] = normalize_text(
                            registration_to_payload(matriz).get("empresa")
                        )
                values["nome_contato"] = normalize_text(pg.get("nome_contato"))
    except Exception:
        pass
    from app.services.lead_actions_storage import get_lead_action
    from app.services.sectors import list_sector_options

    lead_extra = get_lead_action(DEFAULT_TENANT_ID, sheet_row) or {}
    values["setor_id"] = normalize_text(lead_extra.get("setor_id"))
    values["setor"] = normalize_text(lead_extra.get("setor"))
    if not normalize_text(values.get("colaboradores")) and lead_extra.get("colaboradores"):
        values["colaboradores"] = normalize_text(lead_extra.get("colaboradores"))
    if not normalize_text(values.get("valor_proposta")) and lead_extra.get("valor_proposta"):
        values["valor_proposta"] = normalize_text(lead_extra.get("valor_proposta"))
    if not normalize_text(values.get("servico")) and lead_extra.get("servico"):
        values["servico"] = normalize_text(lead_extra.get("servico"))
    # Se nicho custom não está na lista padrão, ainda mostra no select via niche_options merge
    niche_options = get_niche_options()
    if values["nicho"] and values["nicho"] not in niche_options:
        niche_options = list(niche_options) + [values["nicho"]]
    sector_options = list_sector_options()

    cadastro_tipo = resolve_cadastro_tipo(DEFAULT_TENANT_ID, sheet_row, cnpj=values.get("cnpj", ""))
    cadastro_ativo = is_cadastro_ativo(DEFAULT_TENANT_ID, sheet_row)
    from_page = _resolve_edit_from_page(request.query_params.get("from"))
    active_tab = normalize_text(request.query_params.get("tab")) or "dados"
    tab_aliases = {
        "cadastro": "dados",
        "dados": "dados",
        "atividades": "atividades",
        "proposta": "proposta",
        "propostas": "proposta",
        "acesso": "acesso",
        "oppi": "acesso",
        "ponto": "acesso",
        "financeiro": "financeiro",
        "suporte": "suporte",
    }
    active_tab = tab_aliases.get(active_tab, "dados")

    from app.services.oppi_ponto_bridge import refresh_ponto_snapshot
    from app.services.oppi_ponto_client import oppi_ponto_configured

    ponto_snapshot = refresh_ponto_snapshot(sheet_row, cnpj=values.get("cnpj", ""))
    # Recarrega lead_extra após snapshot (funcionários / contrato).
    lead_extra = get_lead_action(DEFAULT_TENANT_ID, sheet_row) or {}
    if not normalize_text(values.get("colaboradores")) and lead_extra.get("colaboradores"):
        values["colaboradores"] = normalize_text(lead_extra.get("colaboradores"))
    if ponto_snapshot.get("funcionarios") and not normalize_text(values.get("colaboradores")):
        values["colaboradores"] = f"{ponto_snapshot['funcionarios']} colaboradores"

    activities_ctx = build_cadastro_activities_context(
        DEFAULT_TENANT_ID,
        sheet_row,
        values.get("empresa", ""),
        lead_created_at=row.get("_data_chamado") or data_chamado_raw or parsed_date,
        from_page=from_page,
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
    billing_plan = load_billing_plan(DEFAULT_TENANT_ID, sheet_row)
    asaas_payments_unique: list = []
    summary_payments = payment_history
    if active_tab == "financeiro":
        asaas_payments = asaas_history_for_cadastro(values, plan=billing_plan)
        asaas_ids = {item.get("asaas_payment_id") for item in payment_history if item.get("asaas_payment_id")}
        asaas_payments_unique = [
            item for item in asaas_payments if item.get("asaas_payment_id") not in asaas_ids
        ]
        summary_payments = payment_history + [
            {
                **item,
                "status": "Pago" if item.get("status") == "Pago" else (
                    "Atrasado" if item.get("status") == "Atrasado" else "Pendente"
                ),
            }
            for item in asaas_payments_unique
        ]
    if closed_services:
        page_ctx["proposals_count"] = len([
            item for item in closed_services
            if any(normalize_text(item.get(key)) for key in ("servico", "valor", "vencimento"))
        ])

    oppi_ponto_ctx = {
        "configured": oppi_ponto_configured(),
        "company_id": ponto_snapshot.get("company_id"),
        "bloqueado": bool(ponto_snapshot.get("bloqueado")),
        "onboarded": bool(lead_extra.get("oppi_ponto_onboarded") or ponto_snapshot.get("company_id")),
        "funcionarios": int(ponto_snapshot.get("funcionarios") or 0),
        "contrato_aceito": ponto_snapshot.get("contrato_aceito"),
        "plano_valor": ponto_snapshot.get("plano_valor") or values.get("valor_proposta") or "",
        "admin_nome": ponto_snapshot.get("admin_nome") or "",
        "admin_email": ponto_snapshot.get("admin_email") or "",
    }

    back_href = _list_url_for_from_page(from_page)
    active_sidebar = {
        "leads": "leads",
        "activities": "activities",
        "funnel": "funnel",
        "attendances": "attendances",
        "overview": "overview",
    }.get(from_page, "leads")

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
                "funnel": "Funil de Vendas",
                "attendances": "Atendimentos",
                "overview": "Visão Geral",
            }.get(from_page, "Empresas"),
            "sheet_row": sheet_row,
            "seller_options": get_seller_options(df),
            "niche_options": niche_options,
            "sector_options": sector_options,
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
            "asaas_payments": asaas_payments_unique,
            "billing_plan": billing_plan,
            "plan_cycle_options": PLAN_CYCLE_OPTIONS,
            "billing_form_options": BILLING_FORM_OPTIONS,
            "asaas_configured": asaas_is_configured(),
            "financial_summary": financial_summary(closed_services, summary_payments),
            "colaborador_options": get_colaborador_options(),
            "vendedor": normalize_text(row.get("_vendedor", "")) or "Sem vendedor",
            "error": request.session.pop("edit_error", ""),
            "success": request.session.pop("edit_success", ""),
            "cadastro_tipo": cadastro_tipo,
            "cadastro_ativo": cadastro_ativo,
            "cadastro_tipo_options": CADASTRO_TIPO_OPTIONS,
            "active_tab": active_tab,
            "oppi_ponto": oppi_ponto_ctx,
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
        if from_page == "funnel":
            return RedirectResponse(url="/funil-de-vendas", status_code=303)
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
            previous_plan = load_billing_plan(DEFAULT_TENANT_ID, sheet_row)
            save_billing_plan(
                DEFAULT_TENANT_ID,
                sheet_row,
                parse_billing_plan_from_form(form, previous=previous_plan),
            )
            request.session["edit_success"] = "Financeiro atualizado com sucesso."
            return RedirectResponse(
                url=_edit_page_url(sheet_row, tab="financeiro", from_page=from_page),
                status_code=303,
            )

        if action == "generate_invoice":
            closed_items = parse_closed_services_from_form(form)
            save_closed_services(DEFAULT_TENANT_ID, sheet_row, closed_items)
            payments = parse_payment_history_from_form(form)
            save_payment_history(DEFAULT_TENANT_ID, sheet_row, payments)
            previous_plan = load_billing_plan(DEFAULT_TENANT_ID, sheet_row)
            plan = save_billing_plan(
                DEFAULT_TENANT_ID,
                sheet_row,
                parse_billing_plan_from_form(form, previous=previous_plan),
            )
            df, columns = get_prepared_data()
            row = _get_row_by_sheet(df, sheet_row)
            values = {
                "empresa": _contract_edit_value(row, columns, "empresa") if row is not None else "",
                "cnpj": _contract_edit_value(row, columns, "cnpj") if row is not None else "",
                "telefone_b2b": _contract_edit_value(row, columns, "telefone_b2b") if row is not None else "",
                "email": _contract_edit_value(row, columns, "email") if row is not None else "",
                "nome_contato": _contract_edit_value(row, columns, "nome_contato") if row is not None else "",
            }
            try:
                from app.services.crm_registrations_storage import (
                    get_registration_by_sheet_row,
                    is_crm_postgres_ready,
                    registration_to_payload,
                )

                if is_crm_postgres_ready():
                    pg_row = get_registration_by_sheet_row(int(sheet_row))
                    if pg_row is not None:
                        pg = registration_to_payload(pg_row)
                        values["empresa"] = normalize_text(pg.get("empresa")) or values["empresa"]
                        values["cnpj"] = normalize_text(pg.get("cnpj")) or values["cnpj"]
                        values["telefone_b2b"] = normalize_text(pg.get("telefone_b2b")) or values["telefone_b2b"]
                        values["email_empresa"] = normalize_text(pg.get("email_empresa")) or values["email"]
                        values["nome_contato"] = normalize_text(pg.get("nome_contato")) or values["nome_contato"]
            except Exception:
                pass
            try:
                result = generate_asaas_invoice(DEFAULT_TENANT_ID, sheet_row, values, plan)
                request.session["edit_success"] = result.get("message") or "Fatura gerada no Asaas."
            except AsaasError as error:
                request.session["edit_error"] = str(error)
            except Exception as error:
                request.session["edit_error"] = f"Não consegui gerar a fatura: {error}"
            return RedirectResponse(
                url=_edit_page_url(sheet_row, tab="financeiro", from_page=from_page),
                status_code=303,
            )

        if action == "save_acesso":
            # Mantém senha atual se o campo vier vazio.
            current_access = load_access_fields(DEFAULT_TENANT_ID, sheet_row)
            if not normalize_text(form_dict.get("senha_acesso")):
                form_dict["senha_acesso"] = current_access.get("senha_acesso", "")
            save_access_fields(DEFAULT_TENANT_ID, sheet_row, form_dict)
            request.session["edit_success"] = "Credenciais de acesso atualizadas."
            return RedirectResponse(
                url=_edit_page_url(sheet_row, tab="acesso", from_page=from_page),
                status_code=303,
            )

        closed_items = parse_closed_services_from_form(form)
        if closed_services_has_data(closed_items):
            primary_closed = save_closed_services(DEFAULT_TENANT_ID, sheet_row, closed_items)
            form_dict["servico"] = primary_closed.get("servico", "")
            form_dict["valor_proposta"] = primary_closed.get("valor", "")
        previous_tipo = resolve_cadastro_tipo(DEFAULT_TENANT_ID, sheet_row, cnpj=form_dict.get("cnpj", ""))
        save_company_edit(sheet_row, form_dict)
        save_cadastro_tipo(DEFAULT_TENANT_ID, sheet_row, form_dict.get("cadastro_tipo", "lead"))
        save_access_fields(DEFAULT_TENANT_ID, sheet_row, form_dict)
        save_nicho(
            DEFAULT_TENANT_ID,
            sheet_row,
            form_dict.get("nicho", ""),
            form_dict.get("nicho_outro", ""),
        )
        from app.services.sectors import get_sector

        setor_id = form_dict.get("setor_id", "")
        setor = get_sector(setor_id)
        save_setor(
            DEFAULT_TENANT_ID,
            sheet_row,
            setor_id,
            (setor or {}).get("name", ""),
        )
        invalidate_sheet_cache()

        onboard_note = ""
        try:
            from app.services.oppi_ponto_bridge import maybe_auto_onboard_on_empresa

            onboard_result = maybe_auto_onboard_on_empresa(
                sheet_row,
                values=form_dict,
                previous_tipo=previous_tipo,
                new_tipo=form_dict.get("cadastro_tipo", "lead"),
                status=form_dict.get("status", ""),
            )
            if onboard_result and onboard_result.get("ok"):
                onboard_note = f" {onboard_result.get('message', '')}"
                if onboard_result.get("password"):
                    onboard_note += f" Senha gerada: {onboard_result['password']}"
            elif onboard_result and not onboard_result.get("ok"):
                onboard_note = f" (Oppi Ponto: {onboard_result.get('message', 'falha no onboard')})"
        except Exception:
            pass

        request.session["edit_success"] = f"Cadastro salvo com sucesso.{onboard_note}"
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
        return RedirectResponse(url=_CADASTRO_LIST_URL, status_code=303)

    try:
        delete_company_registration(DEFAULT_TENANT_ID, sheet_row)
    except ValueError as error:
        request.session["edit_error"] = str(error)
        return RedirectResponse(url=edit_url, status_code=303)
    except Exception as error:
        request.session["edit_error"] = f"Não consegui excluir o cadastro: {error}"
        return RedirectResponse(url=edit_url, status_code=303)

    return RedirectResponse(url=_list_url_for_from_page(from_page), status_code=303)


@router.post("/cadastro/todos/{sheet_row}/ativo")
async def contract_toggle_ativo(
    request: Request,
    sheet_row: int,
    ativo: str = Form("1"),
    from_: str = Form("", alias="from"),
    tab: str = Form("dados"),
):
    redirect = require_auth(request)
    if redirect:
        return redirect

    from_page = _resolve_edit_from_page(from_)
    enabled = normalize_text(ativo).lower() in {"1", "true", "sim", "ativo", "on", "yes"}
    save_cadastro_ativo(DEFAULT_TENANT_ID, sheet_row, enabled)
    request.session["edit_success"] = (
        "Cadastro ativado com sucesso." if enabled else "Cadastro desativado com sucesso."
    )
    return RedirectResponse(
        url=_edit_page_url(sheet_row, tab=normalize_text(tab) or "dados", from_page=from_page),
        status_code=303,
    )


@router.post("/cadastro/todos/{sheet_row}/tipo")
async def contract_update_tipo(request: Request, sheet_row: int, cadastro_tipo: str = Form(...)):
    redirect = require_auth(request)
    if redirect:
        return redirect

    previous_tipo = resolve_cadastro_tipo(DEFAULT_TENANT_ID, sheet_row)
    save_cadastro_tipo(DEFAULT_TENANT_ID, sheet_row, cadastro_tipo)

    try:
        from app.services.oppi_ponto_bridge import maybe_auto_onboard_on_empresa

        df, columns = get_prepared_data()
        row = _get_row_by_sheet(df, sheet_row)
        values = {}
        if row is not None:
            values = {key: _contract_edit_value(row, columns, key) for key in [
                "empresa", "cnpj", "email", "telefone_b2b", "telefone_fixo",
                "socio_1", "email_socio_1", "telefone_socio_1",
                "servico", "valor_proposta", "colaboradores",
            ]}
            values["email_empresa"] = values.get("email", "")
            values.update(load_access_fields(DEFAULT_TENANT_ID, sheet_row))
        result = maybe_auto_onboard_on_empresa(
            sheet_row,
            values=values,
            previous_tipo=previous_tipo,
            new_tipo=cadastro_tipo,
        )
        if result and result.get("ok"):
            msg = result.get("message") or "Oppi Ponto sincronizado."
            if result.get("password"):
                msg += f" Senha: {result['password']}"
            request.session["edit_success"] = msg
        elif result and not result.get("ok"):
            request.session["edit_error"] = result.get("message") or "Falha no Oppi Ponto."
    except Exception:
        pass

    referer = request.headers.get("referer") or f"/cadastro/todos/{sheet_row}/editar"
    return RedirectResponse(url=referer, status_code=303)


@router.post("/cadastro/todos/{sheet_row}/oppi-ponto")
async def contract_oppi_ponto_action(
    request: Request,
    sheet_row: int,
    action: str = Form(...),
    motivo: str = Form("Ação manual pelo CRM Comercial"),
    from_: str = Form("", alias="from"),
    tab: str = Form("acesso"),
):
    redirect = require_auth(request)
    if redirect:
        return redirect

    from_page = _resolve_edit_from_page(from_)
    edit_url = _edit_page_url(sheet_row, tab=normalize_text(tab) or "acesso", from_page=from_page)

    df, columns = get_prepared_data()
    row = _get_row_by_sheet(df, sheet_row)
    if row is None:
        request.session["edit_error"] = "Cadastro não encontrado."
        return RedirectResponse(url=_CADASTRO_LIST_URL, status_code=303)

    values = {key: _contract_edit_value(row, columns, key) for key in [
        "empresa", "cnpj", "email", "telefone_b2b", "telefone_fixo",
        "socio_1", "email_socio_1", "telefone_socio_1",
        "servico", "valor_proposta", "colaboradores",
    ]}
    values["email_empresa"] = values.get("email", "")
    values.update(load_access_fields(DEFAULT_TENANT_ID, sheet_row))
    from app.services.lead_actions_storage import get_lead_action

    lead_extra = get_lead_action(DEFAULT_TENANT_ID, sheet_row) or {}
    if not normalize_text(values.get("colaboradores")) and lead_extra.get("colaboradores"):
        values["colaboradores"] = normalize_text(lead_extra.get("colaboradores"))

    try:
        from app.services.oppi_ponto_bridge import (
            block_company_on_ponto,
            release_payment_on_ponto,
            sync_or_onboard_company,
            unblock_company_on_ponto,
        )
        from app.services.oppi_ponto_client import OppiPontoError
    except Exception as error:
        request.session["edit_error"] = f"Falha ao carregar integração Oppi Ponto: {error}"
        return RedirectResponse(url=edit_url, status_code=303)

    try:
        act = normalize_text(action).lower()
        if act == "bloquear":
            result = block_company_on_ponto(sheet_row, cnpj=values.get("cnpj", ""))
            request.session["edit_success"] = f"Empresa bloqueada no Oppi Ponto (#{result['company_id']})."
        elif act == "desbloquear":
            result = unblock_company_on_ponto(sheet_row, cnpj=values.get("cnpj", ""))
            request.session["edit_success"] = f"Empresa desbloqueada no Oppi Ponto (#{result['company_id']})."
        elif act in {"liberar_pagamento", "liberar"}:
            result = release_payment_on_ponto(
                sheet_row,
                cnpj=values.get("cnpj", ""),
                motivo=motivo or "Liberação manual pelo CRM Comercial",
            )
            request.session["edit_success"] = (
                f"Pagamento liberado no Oppi Ponto (#{result['company_id']}). Acesso restaurado."
            )
        elif act in {"onboard", "sincronizar"}:
            closed = load_closed_services(
                DEFAULT_TENANT_ID,
                sheet_row,
                servico=values.get("servico", ""),
                valor_proposta=values.get("valor_proposta", ""),
            )
            result = sync_or_onboard_company(
                sheet_row,
                values=values,
                closed_services=closed,
            )
            msg = result.get("message") or "Oppi Ponto sincronizado."
            if result.get("password"):
                msg += f" Senha gerada: {result['password']}"
            request.session["edit_success"] = msg
        else:
            request.session["edit_error"] = f"Ação desconhecida: {action}"
    except OppiPontoError as error:
        request.session["edit_error"] = str(error)
    except Exception as error:
        request.session["edit_error"] = f"Falha Oppi Ponto: {error}"

    return RedirectResponse(url=edit_url, status_code=303)


@router.post("/cadastro/todos/{sheet_row}/status", response_class=HTMLResponse)
async def contract_update_status(request: Request, sheet_row: int, status: str = Form(...)):
    redirect = require_auth(request)
    if redirect:
        return redirect

    df, columns = get_prepared_data()
    row = _get_row_by_sheet(df, sheet_row)
    if row is None:
        return RedirectResponse(url=_CADASTRO_LIST_URL, status_code=303)

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
        return RedirectResponse(url=request.headers.get("referer", _CADASTRO_LIST_URL), status_code=303)

    status_context = {
        "sheet_row": sheet_row,
        "status": new_status,
        "status_class": status_badge_class(new_status),
        "status_options": STATUS_OPTIONS,
    }

    if request.headers.get("HX-Request"):
        return render(request, "partials/contracts_status_cell.html", status_context)

    return RedirectResponse(url=request.headers.get("referer", _CADASTRO_LIST_URL), status_code=303)


@router.post("/cadastro/todos/atualizar")
async def contracts_refresh(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect
    invalidate_sheet_cache()
    return RedirectResponse(url=f"{_CADASTRO_LIST_URL}?refresh=1", status_code=303)
