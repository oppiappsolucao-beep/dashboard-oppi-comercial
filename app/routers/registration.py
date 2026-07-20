from datetime import date

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.dependencies import get_prepared_data, is_admin, require_auth
from app.services.activities_storage import DEFAULT_TENANT_ID
from app.services.activity_service import criar_atividade
from app.services.commercial_services import get_commercial_service_options
from app.services.crm_validation_service import get_actions_for_stage, normalize_legacy_stage
from app.services.legacy_core import DuplicateRegistrationError, STATUS_OPTIONS, get_colaborador_options, normalize_text
from app.services.registration import (
    CADASTRO_TIPO_OPTIONS,
    get_seller_options,
    infer_partners_count,
    save_cadastro_tipo,
    save_new_company,
)
from app.templating import render
from config.crm_options import CHANNEL_OPTIONS, PIPELINE_STAGE_OPTIONS, PRIORITY_OPTIONS

router = APIRouter()


def _registration_page_context(request: Request, df, *, error: str = "", values: dict | None = None) -> dict:
    values = values or {}
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
    return {
        "active_page": "registration_new",
        "seller_options": get_seller_options(df),
        "status_options": STATUS_OPTIONS,
        "service_options": get_commercial_service_options(),
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
        "cadastro_tipo": cadastro_tipo,
        "cadastro_tipo_options": CADASTRO_TIPO_OPTIONS,
        "error": error or request.session.pop("registration_error", ""),
    }


@router.get("/cadastro/novo", response_class=HTMLResponse)
async def new_registration_page(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect

    df, _columns = get_prepared_data()
    values = {key: normalize_text(value) for key, value in request.query_params.items()}
    return render(request, "registration/new.html", _registration_page_context(request, df, values=values))


@router.post("/cadastro/novo")
async def new_registration_submit(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect

    form = await request.form()
    form_dict = dict(form)
    user = normalize_text(request.session.get("username", "")) or "Usuário"

    try:
        sheet_row = save_new_company(form_dict)
        save_cadastro_tipo(DEFAULT_TENANT_ID, sheet_row, form_dict.get("cadastro_tipo", "lead"))
        empresa = normalize_text(form_dict.get("empresa"))
        status = normalize_text(form_dict.get("status"))

        create_activity = normalize_text(form_dict.get("create_first_activity")) in {"1", "on", "true", "yes"}
        activity_warning = ""
        if create_activity:
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
                activity_warning = f" Empresa cadastrada, mas a primeira atividade não foi criada: {activity_error}"

        request.session["company_registration_success"] = (
            f'Empresa "{empresa}" cadastrada com sucesso na planilha com o status "{status}".{activity_warning}'
        )
        return RedirectResponse(url="/visao-geral", status_code=303)
    except DuplicateRegistrationError as error:
        request.session["registration_error"] = str(error)
    except ValueError as error:
        request.session["registration_error"] = str(error)
    except Exception as error:
        request.session["registration_error"] = f"Não consegui cadastrar a empresa: {error}"

    return RedirectResponse(url="/cadastro/novo", status_code=303)
