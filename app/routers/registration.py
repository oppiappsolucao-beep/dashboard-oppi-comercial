from datetime import date

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.dependencies import get_prepared_data, is_admin, require_auth
from app.services.activities_storage import DEFAULT_TENANT_ID
from app.services.activity_service import criar_atividade
from app.services.closed_services import PAYMENT_METHOD_OPTIONS, closed_services_has_data, closed_services_sheet_values, load_closed_services, parse_closed_services_from_form, save_closed_services
from app.services.commercial_services import get_commercial_service_options
from app.services.crm_validation_service import get_actions_for_stage, normalize_legacy_stage
from app.services.legacy_core import DuplicateRegistrationError, STATUS_OPTIONS, get_colaborador_options, normalize_text
from app.services.registration import (
    CADASTRO_TIPO_OPTIONS,
    NICHE_OPTIONS,
    build_cadastro_new_page_context,
    get_seller_options,
    infer_partners_count,
    save_cadastro_tipo,
    save_new_company,
    save_nicho,
)
from app.templating import render
from config.crm_options import CHANNEL_OPTIONS, PIPELINE_STAGE_OPTIONS, PRIORITY_OPTIONS

router = APIRouter()


def _resolve_registration_from_page(value: str) -> str:
    normalized = normalize_text(value)
    return normalized if normalized in {"leads", "activities"} else ""


def _edit_page_url(sheet_row: int, *, from_page: str = "") -> str:
    query = f"?from={from_page}" if from_page else ""
    return f"/cadastro/todos/{sheet_row}/editar{query}"


def _normalize_registration_values(values: dict) -> dict:
    normalized = {key: normalize_text(value) for key, value in values.items()}
    normalized["email"] = normalize_text(normalized.get("email") or normalized.get("email_empresa"))
    return normalized


def _registration_page_context(request: Request, df, *, error: str = "", values: dict | None = None) -> dict:
    values = _normalize_registration_values(values or {})
    default_stage = normalize_legacy_stage(values.get("status")) or "Novo Lead"
    activity_actions = get_actions_for_stage(default_stage)
    default_activity_action = (
        normalize_text(values.get("activity_action"))
        or (activity_actions[0] if activity_actions else "Fazer primeiro contato")
    )
    default_date = normalize_text(values.get("data_chamado") or values.get("activity_date")) or date.today().isoformat()
    cadastro_tipo = normalize_text(values.get("cadastro_tipo")).lower()
    if cadastro_tipo not in {"lead", "empresa"}:
        cadastro_tipo = "lead"

    seller_options = get_seller_options(df)
    current_user = normalize_text(request.session.get("username", "")) or "Usuário"
    vendedor = normalize_text(values.get("vendedor"))
    if not vendedor:
        vendedor = current_user if current_user in seller_options else (seller_options[0] if seller_options else "Sem vendedor")

    from_page = _resolve_registration_from_page(values.get("from") or request.query_params.get("from"))
    active_page = "leads" if from_page == "leads" else "registration_new"
    back_href = {
        "leads": "/leads-e-empresas",
        "activities": "/atividades",
    }.get(from_page, "/cadastro/todos")
    back_label = {
        "leads": "Empresas",
        "activities": "Atividades",
    }.get(from_page, "Todos os cadastros")

    page_ctx = build_cadastro_new_page_context(
        values=values,
        cadastro_tipo=cadastro_tipo,
        vendedor=vendedor,
    )

    return {
        "active_page": active_page,
        "from_page": from_page,
        "back_href": back_href,
        "back_label": back_label,
        "seller_options": seller_options,
        "niche_options": NICHE_OPTIONS,
        "status_options": STATUS_OPTIONS,
        "service_options": get_commercial_service_options(),
        "payment_method_options": PAYMENT_METHOD_OPTIONS,
        "colaborador_options": get_colaborador_options(),
        "pipeline_stages": PIPELINE_STAGE_OPTIONS,
        "channel_options": CHANNEL_OPTIONS,
        "priority_options": PRIORITY_OPTIONS,
        "activity_actions": activity_actions,
        "default_activity_action": default_activity_action,
        "today": default_date,
        "default_time": normalize_text(values.get("activity_time")) or "09:00",
        "partners_count": infer_partners_count(values),
        "values": values,
        "vendedor": vendedor,
        "cadastro_tipo": cadastro_tipo,
        "cadastro_tipo_options": CADASTRO_TIPO_OPTIONS,
        "closed_services": load_closed_services(DEFAULT_TENANT_ID, 0),
        "error": error or request.session.pop("registration_error", ""),
        **page_ctx,
    }


@router.get("/cadastro/novo", response_class=HTMLResponse)
async def new_registration_page(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect

    # Abre o formulário sem forçar leitura fresca da planilha (evita 429).
    # Vendedores vêm dos usuários da conta; planilha só entra se o cache já existir.
    try:
        df, _columns = get_prepared_data()
    except Exception:
        import pandas as pd

        df = pd.DataFrame()
    values = {key: normalize_text(value) for key, value in request.query_params.items()}
    return render(request, "registration/new.html", _registration_page_context(request, df, values=values))


@router.post("/cadastro/novo")
async def new_registration_submit(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect

    form = await request.form()
    form_dict = dict(form)
    form_dict["email_empresa"] = form_dict.pop("email", form_dict.get("email_empresa", ""))
    user = normalize_text(request.session.get("username", "")) or "Usuário"
    from_page = _resolve_registration_from_page(form_dict.get("from"))

    try:
        closed_items = parse_closed_services_from_form(form)
        if closed_services_has_data(closed_items):
            mirror = closed_services_sheet_values(closed_items)
            form_dict["servico"] = mirror.get("servico", "")
            form_dict["valor_proposta"] = mirror.get("valor_proposta", "")
        sheet_row = save_new_company(form_dict)
        save_cadastro_tipo(DEFAULT_TENANT_ID, sheet_row, form_dict.get("cadastro_tipo", "lead"))
        from app.services.registration import save_access_fields
        if int(sheet_row or 0) > 0:
            save_access_fields(DEFAULT_TENANT_ID, sheet_row, form_dict)
            save_nicho(DEFAULT_TENANT_ID, sheet_row, form_dict.get("nicho", ""))
        if closed_services_has_data(closed_items):
            save_closed_services(DEFAULT_TENANT_ID, sheet_row, closed_items)

        empresa = normalize_text(form_dict.get("empresa"))
        status = normalize_text(form_dict.get("status"))

        create_activity = normalize_text(form_dict.get("create_first_activity")) in {"1", "on", "true", "yes"}
        activity_warning = ""
        if create_activity and int(sheet_row or 0) > 0:
            stage = normalize_legacy_stage(form_dict.get("status")) or "Novo Lead"
            scheduled_date = normalize_text(form_dict.get("activity_date")) or date.today().isoformat()
            scheduled_time = normalize_text(form_dict.get("activity_time")) or "09:00"
            responsible = (
                normalize_text(form_dict.get("activity_responsible"))
                or normalize_text(form_dict.get("vendedor"))
            )
            _, activity_error = criar_atividade(
                DEFAULT_TENANT_ID,
                {
                    "sheet_row": sheet_row,
                    "empresa": empresa,
                    "contato": normalize_text(form_dict.get("socio_1")) or "—",
                    "stage": stage,
                    "activity_type": "Contato",
                    "process_action": normalize_text(form_dict.get("activity_action")) or "Fazer primeiro contato",
                    "channel": normalize_text(form_dict.get("activity_channel")) or "WhatsApp",
                    "assigned_user_id": responsible,
                    "scheduled_date": scheduled_date,
                    "scheduled_time": scheduled_time,
                    "status": "pendente",
                    "priority": normalize_text(form_dict.get("activity_priority")) or "Média",
                    "description": normalize_text(form_dict.get("activity_description")),
                    "next_action": normalize_text(form_dict.get("activity_next_action")),
                },
                user,
                is_admin_user=is_admin(request),
            )
            if activity_error:
                activity_warning = f" Cadastro criado, mas a primeira atividade não foi criada: {activity_error}"

        if int(sheet_row or 0) < 0:
            request.session["company_registration_success"] = (
                f'"{empresa}" foi salvo e já aparece no sistema. '
                f"A sincronização com a planilha acontece automaticamente.{activity_warning}"
            )
            tab = "empresas" if normalize_text(form_dict.get("cadastro_tipo")).lower() == "empresa" else "leads"
            return RedirectResponse(url=f"/leads-e-empresas?tab={tab}", status_code=303)

        request.session["company_registration_success"] = (
            f'"{empresa}" cadastrado com sucesso com o status "{status}".{activity_warning}'
        )
        return RedirectResponse(url=_edit_page_url(sheet_row, from_page=from_page), status_code=303)
    except DuplicateRegistrationError as error:
        request.session["registration_error"] = str(error)
    except ValueError as error:
        request.session["registration_error"] = str(error)
    except Exception as error:
        message = str(error)
        if "429" in message or "Quota exceeded" in message:
            request.session["registration_error"] = (
                "A planilha está temporariamente ocupada. Aguarde cerca de 30 segundos e salve novamente."
            )
        else:
            request.session["registration_error"] = (
                "Não consegui cadastrar agora. Aguarde alguns segundos e tente salvar novamente."
            )

    redirect_params = f"?from={from_page}" if from_page else ""
    return RedirectResponse(url=f"/cadastro/novo{redirect_params}", status_code=303)
