import re
from datetime import date

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.dependencies import get_prepared_data, require_auth
from app.services.activities_storage import DEFAULT_TENANT_ID
from app.services.closed_services import PAYMENT_METHOD_OPTIONS, closed_services_has_data, closed_services_sheet_values, load_closed_services, parse_closed_services_from_form, save_closed_services
from app.services.commercial_services import get_commercial_service_catalog, get_commercial_service_options
from app.services.crm_validation_service import get_actions_for_stage, normalize_legacy_stage
from app.services.legacy_core import DuplicateRegistrationError, STATUS_OPTIONS, get_colaborador_options, normalize_text
from app.services.cadastro_billing import (
    BILLING_FORM_OPTIONS,
    PLAN_CYCLE_OPTIONS,
    parse_billing_plan_from_form,
    save_billing_plan,
)
from app.services.registration import (
    CADASTRO_TIPO_OPTIONS,
    build_cadastro_new_page_context,
    get_niche_options,
    get_seller_options,
    infer_partners_count,
    save_cadastro_tipo,
    save_new_company,
    save_nicho,
    save_setor,
)
from app.templating import render
from config.crm_options import CHANNEL_OPTIONS, PIPELINE_STAGE_OPTIONS, PRIORITY_OPTIONS

router = APIRouter()


def _save_on_raissa_note(form_dict: dict, billing_plan: dict | None, sheet_row: int | None = None) -> str:
    try:
        from app.services.raissa_company_sync import save_registration_on_raissa

        result = save_registration_on_raissa(form_dict, billing_plan, crm_sheet_row=sheet_row)
    except Exception:
        return " Não consegui gravar na aba Raissa agora."
    if result.get("ok"):
        aba = result.get("aba") or "Raissa"
        return f" Dados gerais e financeiro gravados na aba {aba}. O cliente entra em Empresas."
    aviso = normalize_text(result.get("aviso"))
    return f" {aviso}" if aviso else ""


def _resolve_registration_from_page(value: str) -> str:
    normalized = normalize_text(value)
    return normalized if normalized in {"leads", "activities"} else ""


def _registration_closes() -> dict:
    try:
        from app.services.cadastro_closes import build_registration_closes

        return build_registration_closes()
    except Exception:
        return {
            "pending": [],
            "pending_total": 0,
            "registered": [],
            "registered_total": 0,
            "month_label": "",
        }


def _training_trainers() -> list[dict]:
    from app.services.org_registry import list_people

    return [
        {"id": person["id"], "name": person["name"]}
        for person in list_people("treinador")
        if person.get("name")
    ]


def _validate_training(form_dict: dict) -> None:
    responsible = normalize_text(form_dict.get("training_responsible"))
    if len(responsible) < 2:
        raise ValueError("Informe o nome do responsável do treinamento.")
    try:
        quantity = int(normalize_text(form_dict.get("training_employees")))
    except ValueError:
        quantity = 0
    if quantity < 1:
        raise ValueError("Informe a quantidade de funcionários do treinamento.")
    if not normalize_text(form_dict.get("training_trainer_id")):
        raise ValueError("Selecione o treinador.")
    if len(normalize_text(form_dict.get("training_link"))) < 8:
        raise ValueError("Informe o link da videoconferência.")
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", normalize_text(form_dict.get("training_date"))):
        raise ValueError("Informe a data do treinamento.")
    if not re.match(r"^\d{2}:\d{2}$", normalize_text(form_dict.get("training_time"))):
        raise ValueError("Informe o horário do treinamento.")


def schedule_training(form_dict: dict, sheet_row: int, empresa: str, created_by: str) -> None:
    """Abre a ordem de treinamento na agenda do treinador escolhido."""
    from app.services.org_registry import ensure_training_slot, get_person
    from app.services.service_orders import create_service_order, trainer_is_busy

    _validate_training(form_dict)
    trainer = get_person(form_dict.get("training_trainer_id", ""))
    if trainer is None or trainer.get("kind") != "treinador" or not trainer.get("sector_name"):
        raise ValueError("Selecione um treinador cadastrado em Sistema.")
    day = normalize_text(form_dict.get("training_date"))
    hour = normalize_text(form_dict.get("training_time"))
    ensure_training_slot(trainer, day, hour)
    if trainer_is_busy(trainer.get("name", ""), day, hour):
        raise ValueError(f"{trainer.get('name')} já tem um treinamento nesse horário.")
    quantity = normalize_text(form_dict.get("training_employees"))
    responsible = normalize_text(form_dict.get("training_responsible"))
    link = normalize_text(form_dict.get("training_link"))
    description = (
        f"Responsável: {responsible}. "
        f"Quantidade de funcionários: {quantity}. "
        f"Treinador: {trainer['name']}. "
        f"Videoconferência: {link}. "
        f"Horário: {hour}."
    )
    create_service_order(
        tenant_id=DEFAULT_TENANT_ID,
        sheet_row=sheet_row,
        empresa=empresa,
        subject="Treinamento",
        description=description,
        sector=trainer["sector_name"],
        scheduled_date=day,
        responsible=trainer["name"],
        priority="Média",
        created_by=created_by,
    )


def _creator_assignment(request: Request, vendedor: str) -> tuple[str, str] | None:
    from app.services.org_registry import list_people

    people = [person for person in list_people() if person.get("sector_name") and person["sector_name"] != "—"]
    sector = normalize_text(request.session.get("org_sector_name"))
    name = normalize_text(request.session.get("org_person_name"))
    if sector and name:
        for person in people:
            if person["sector_name"].lower() == sector.lower() and person["name"].lower() == name.lower():
                return person["sector_name"], person["name"]
    seller = normalize_text(vendedor)
    if seller:
        for person in people:
            if person["name"].lower() == seller.lower():
                return person["sector_name"], person["name"]
    return None


def _open_registration_orders(request: Request, form_dict: dict, sheet_row: int, empresa: str, created_by: str) -> str:
    from app.services.org_registry import get_person
    from app.services.service_orders import create_service_order

    notes: list[str] = []
    creator = _creator_assignment(request, form_dict.get("vendedor", ""))
    if creator is None:
        notes.append("a OS de entrada não foi gerada porque quem salvou não está em um setor")
    else:
        try:
            create_service_order(
                tenant_id=DEFAULT_TENANT_ID,
                sheet_row=sheet_row,
                empresa=empresa,
                subject="Entrada",
                description="Cliente entrou no cadastro.",
                sector=creator[0],
                scheduled_date=date.today().isoformat(),
                responsible=creator[1],
                priority="Média",
                created_by=created_by,
            )
        except ValueError as error:
            notes.append(f"a OS de entrada não foi gerada: {error}")

    if normalize_text(form_dict.get("create_training")) not in {"1", "on", "true", "yes"}:
        return f" Atenção: {'; '.join(notes)}." if notes else ""

    trainer = get_person(form_dict.get("training_trainer_id", ""))
    if trainer is None or trainer.get("kind") != "treinador" or not trainer.get("sector_name"):
        notes.append("a OS de treinamento não foi gerada: selecione um treinador cadastrado em Sistema")
        return f" Atenção: {'; '.join(notes)}." if notes else ""

    quantity = normalize_text(form_dict.get("training_employees"))
    responsible = normalize_text(form_dict.get("training_responsible"))
    link = normalize_text(form_dict.get("training_link"))
    hour = normalize_text(form_dict.get("training_time")) or "09:00"
    description = (
        f"Responsável: {responsible}. "
        f"Quantidade de funcionários: {quantity}. "
        f"Treinador: {trainer['name']}. "
        f"Videoconferência: {link}. "
        f"Horário: {hour}."
    )
    try:
        create_service_order(
            tenant_id=DEFAULT_TENANT_ID,
            sheet_row=sheet_row,
            empresa=empresa,
            subject="Treinamento",
            description=description,
            sector=trainer["sector_name"],
            scheduled_date=normalize_text(form_dict.get("training_date")),
            responsible=trainer["name"],
            priority="Média",
            created_by=created_by,
        )
    except ValueError as error:
        notes.append(f"a OS de treinamento não foi gerada: {error}")
    return f" Atenção: {'; '.join(notes)}." if notes else ""


def _edit_page_url(sheet_row: int, *, from_page: str = "", tab: str = "") -> str:
    params = []
    if tab:
        params.append(f"tab={tab}")
    if from_page:
        params.append(f"from={from_page}")
    query = f"?{'&'.join(params)}" if params else ""
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
    from app.dependencies import get_session_user

    session_user = get_session_user(request) or {}
    from app.services.registration import seller_from_session_user

    vendedor = seller_from_session_user(session_user)

    from app.services.sectors import list_sector_options

    sector_options = list_sector_options()
    from_page = _resolve_registration_from_page(values.get("from") or request.query_params.get("from"))
    active_page = "leads" if from_page == "leads" else "registration_new"
    back_href = {
        "leads": "/leads-e-empresas",
        "activities": "/atividades",
    }.get(from_page, "/atividades")
    back_label = {
        "leads": "Empresas",
        "activities": "Kanban",
    }.get(from_page, "Kanban")

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
        "niche_options": get_niche_options(),
        "sector_options": sector_options,
        "status_options": STATUS_OPTIONS,
        "service_options": get_commercial_service_options(),
        "service_catalog": get_commercial_service_catalog(),
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
        "lock_responsible": True,
        "trainers": _training_trainers(),
        "cadastro_tipo": cadastro_tipo,
        "cadastro_tipo_options": CADASTRO_TIPO_OPTIONS,
        "closed_services": load_closed_services(DEFAULT_TENANT_ID, 0),
        "billing_plan": {
            "ciclo": normalize_text(values.get("billing_ciclo")).lower(),
            "forma": normalize_text(values.get("billing_forma")).lower(),
            "servico": normalize_text(values.get("billing_servico")),
            "valor": normalize_text(values.get("billing_valor")),
            "vencimento": normalize_text(values.get("billing_vencimento"))[:10],
        },
        "plan_cycle_options": PLAN_CYCLE_OPTIONS,
        "billing_form_options": BILLING_FORM_OPTIONS,
        "error": error or request.session.pop("registration_error", ""),
        "registration_closes": _registration_closes(),
        **page_ctx,
    }


@router.get("/cadastro/api/cnpj/{cnpj}")
async def api_cnpj_lookup(request: Request, cnpj: str):
    redirect = require_auth(request)
    if redirect:
        return JSONResponse({"ok": False, "error": "Não autenticado."}, status_code=401)
    from app.services.cnpj_lookup import CnpjLookupError, lookup_cnpj

    try:
        payload = lookup_cnpj(cnpj)
    except CnpjLookupError as error:
        return JSONResponse({"ok": False, "error": str(error)}, status_code=422)
    except Exception:
        return JSONResponse(
            {"ok": False, "error": "Não consegui consultar o CNPJ agora. Tente de novo em instantes."},
            status_code=502,
        )
    return JSONResponse({"ok": True, **payload})


def _complete_pasted_registration(fields: dict) -> None:
    """Completa nicho, abertura e responsável legal com a consulta do CNPJ."""
    cnpj = normalize_text(fields.get("cnpj"))
    if len("".join(ch for ch in cnpj if ch.isdigit())) != 14:
        return
    try:
        from app.services.cnpj_lookup import CnpjLookupError, lookup_cnpj

        looked = lookup_cnpj(cnpj)
    except CnpjLookupError:
        looked = {}
    except Exception:
        looked = {}
    for key in (
        "nicho",
        "data_abertura",
        "nome_fantasia",
        "capital",
        "responsavel_legal",
        "socio_1",
        "cpf_socio_1",
        "email",
        "email_socio_1",
        "email_login_gestor",
        "email_confirmacao_admin",
        "email_cobranca",
    ):
        if not normalize_text(fields.get(key)) and normalize_text(looked.get(key)):
            fields[key] = looked[key]
    if not normalize_text(fields.get("senha_acesso")) and normalize_text(looked.get("senha_acesso")):
        fields["senha_acesso"] = looked["senha_acesso"]
    if fields.get("socio_1") and not fields.get("quantidade_socios"):
        fields["quantidade_socios"] = "1"
    if fields.get("socio_1") and not fields.get("responsavel_legal"):
        fields["responsavel_legal"] = fields["socio_1"]
    from app.services.cadastro_bot import ensure_login_fields

    ensure_login_fields(fields)


@router.get("/cadastro/bot/dados-gerais")
async def cadastro_bot_formulario(request: Request):
    redirect = require_auth(request)
    if redirect:
        return JSONResponse({"campos": [], "aviso": "Faça login para ver o formulário do bot."}, status_code=401)
    from app.services.cadastro_bot import formulario_dados_gerais

    return JSONResponse(formulario_dados_gerais())


@router.post("/cadastro/bot/dados-gerais")
async def cadastro_bot_dados_gerais(request: Request):
    redirect = require_auth(request)
    if redirect:
        return JSONResponse({"fields": {}, "preenchidos": [], "aviso": "Faça login para usar o bot."}, status_code=401)
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    text = payload.get("text") if isinstance(payload, dict) else ""
    from app.services.cadastro_bot import read_dados_gerais

    result = read_dados_gerais(str(text or ""), niche_options=get_niche_options())
    _complete_pasted_registration(result.get("fields") or {})
    return JSONResponse(result)


@router.get("/cadastro/api/empresas-matriz")
async def api_empresas_matriz(request: Request, q: str = "", exclude: int | None = None):
    redirect = require_auth(request)
    if redirect:
        return JSONResponse({"items": []}, status_code=401)
    try:
        from app.services.crm_registrations_storage import (
            is_crm_postgres_ready,
            search_matriz_companies,
        )

        if not is_crm_postgres_ready():
            return JSONResponse({"items": []})
        items = search_matriz_companies(
            q or "",
            exclude_sheet_row=exclude,
            limit=20,
            tenant_id=DEFAULT_TENANT_ID,
        )
        return JSONResponse({"items": items})
    except Exception:
        return JSONResponse({"items": []})


def _apply_lead_status(form_dict: dict, user: str) -> None:
    """Na tela de cadastro, o status escolhido muda o local do lead no Kanban."""
    queue_id = normalize_text(form_dict.get("kanban_queue"))
    if queue_id == "concluida":
        form_dict["status"] = "Fechado"
    order_id = normalize_text(form_dict.get("os"))
    if not order_id or not queue_id:
        return
    try:
        from app.services.service_orders import get_order_detail, move_service_order, queue_choices

        detail = get_order_detail(order_id)
        if not detail:
            return
        sector_id, _queues = queue_choices(detail.get("sector") or "")
        if not sector_id:
            return
        if normalize_text(detail.get("queue_id")) == queue_id:
            return
        move_service_order(
            order_id,
            queue_id,
            sector_id,
            detail.get("sector") or "",
            author=user or "Usuário",
            reopen=normalize_text(detail.get("queue_id")) == "concluida",
        )
    except Exception:
        return


@router.post("/cadastro/lead/status")
async def registration_lead_status(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect
    form = await request.form()
    user = normalize_text(request.session.get("org_person_name")) or normalize_text(request.session.get("username")) or "Usuário"
    _apply_lead_status(
        {"os": form.get("order_id"), "kanban_queue": form.get("queue_id"), "status": ""},
        user,
    )
    return RedirectResponse(url="/cadastro/novo", status_code=303)


@router.get("/cadastro/treinadores/{trainer_id}/horarios")
async def trainer_open_hours(request: Request, trainer_id: str, data: str = ""):
    if not request.session.get("authenticated"):
        return JSONResponse({"slots": [], "message": "Faça login para ver os horários."}, status_code=401)
    from app.services.access_scope import enforce_access
    from app.services.org_registry import available_training_slots, get_person

    if enforce_access(request):
        return JSONResponse({"slots": [], "message": "Sem acesso aos horários."}, status_code=403)
    trainer = get_person(trainer_id)
    if trainer is None or trainer.get("kind") != "treinador":
        return JSONResponse({"slots": [], "message": "Treinador não encontrado."}, status_code=404)
    slots, message = available_training_slots(trainer, data)
    return JSONResponse({"slots": slots, "message": message})


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
    if not normalize_text(form_dict.get("vendedor")):
        from app.dependencies import get_session_user
        from app.services.registration import seller_from_session_user

        form_dict["vendedor"] = seller_from_session_user(get_session_user(request))
    user = normalize_text(request.session.get("username", "")) or "Usuário"
    from_page = _resolve_registration_from_page(form_dict.get("from"))

    try:
        if normalize_text(form_dict.get("create_training")) in {"1", "on", "true", "yes"}:
            _validate_training(form_dict)
        closed_items = parse_closed_services_from_form(form)
        if closed_services_has_data(closed_items):
            mirror = closed_services_sheet_values(closed_items)
            form_dict["servico"] = mirror.get("servico", "")
            form_dict["valor_proposta"] = mirror.get("valor_proposta", "")
        _apply_lead_status(form_dict, user)
        sheet_row = save_new_company(form_dict, mirror_sheet=False)
        if int(sheet_row or 0) > 0 and normalize_text(form_dict.get("os")):
            from app.services.cadastro_closes import remember_lead_order

            remember_lead_order(int(sheet_row), form_dict.get("os", ""))
        save_cadastro_tipo(
            DEFAULT_TENANT_ID,
            sheet_row,
            form_dict.get("cadastro_tipo", "lead"),
            mirror_sheet=False,
        )
        from app.services.registration import save_access_fields
        if int(sheet_row or 0) > 0:
            save_access_fields(DEFAULT_TENANT_ID, sheet_row, form_dict, mirror_sheet=False)
            save_nicho(
                DEFAULT_TENANT_ID,
                sheet_row,
                form_dict.get("nicho", ""),
                form_dict.get("nicho_outro", ""),
                mirror_sheet=False,
            )
            from app.services.sectors import get_sector

            setor_id = form_dict.get("setor_id", "")
            setor = get_sector(setor_id)
            save_setor(
                DEFAULT_TENANT_ID,
                sheet_row,
                setor_id,
                (setor or {}).get("name", ""),
                mirror_sheet=False,
            )
            from app.services.crm_registrations_storage import _mirror_registration_to_folha1

            _mirror_registration_to_folha1(int(sheet_row))
        if closed_services_has_data(closed_items):
            save_closed_services(DEFAULT_TENANT_ID, sheet_row, closed_items, sync_sheet=False)
        billing_plan = None
        if int(sheet_row or 0) != 0 and (
            normalize_text(form.get("billing_forma"))
            or normalize_text(form.get("billing_valor"))
            or normalize_text(form.get("billing_servico"))
        ):
            billing_plan = save_billing_plan(
                DEFAULT_TENANT_ID,
                sheet_row,
                parse_billing_plan_from_form(form),
            )
        raissa_note = _save_on_raissa_note(form_dict, billing_plan, sheet_row if int(sheet_row or 0) > 0 else None)

        empresa = normalize_text(form_dict.get("empresa"))
        status = normalize_text(form_dict.get("status"))

        order_warning = ""
        if int(sheet_row or 0) != 0:
            order_warning = _open_registration_orders(request, form_dict, int(sheet_row), empresa, user)

        if int(sheet_row or 0) < 0:
            request.session["company_registration_success"] = (
                f'"{empresa}" foi salvo e já aparece no sistema. '
                f"A sincronização com a planilha acontece automaticamente.{raissa_note}{order_warning}"
            )
            tab = "empresas" if normalize_text(form_dict.get("cadastro_tipo")).lower() == "empresa" else "leads"
            try:
                if normalize_text(form_dict.get("cadastro_tipo")).lower() == "empresa":
                    from app.services.oppi_ponto_bridge import maybe_auto_onboard_on_empresa

                    onboard_result = maybe_auto_onboard_on_empresa(
                        sheet_row,
                        values=form_dict,
                        previous_tipo="lead",
                        new_tipo="empresa",
                        status=form_dict.get("status", ""),
                    )
                    if onboard_result and onboard_result.get("ok") and onboard_result.get("password"):
                        request.session["company_registration_success"] += (
                            f" Oppi Ponto: {onboard_result.get('message')} Senha: {onboard_result['password']}"
                        )
                    elif onboard_result and onboard_result.get("ok"):
                        request.session["company_registration_success"] += (
                            f" Oppi Ponto: {onboard_result.get('message')}"
                        )
            except Exception:
                pass
            return RedirectResponse(url=f"/leads-e-empresas?tab={tab}", status_code=303)

        request.session["company_registration_success"] = (
            f'"{empresa}" cadastrado com sucesso com o status "{status}".{raissa_note}{order_warning}'
        )
        try:
            if normalize_text(form_dict.get("cadastro_tipo")).lower() == "empresa":
                from app.services.oppi_ponto_bridge import maybe_auto_onboard_on_empresa

                onboard_result = maybe_auto_onboard_on_empresa(
                    sheet_row,
                    values=form_dict,
                    previous_tipo="lead",
                    new_tipo="empresa",
                    status=form_dict.get("status", ""),
                )
                if onboard_result and onboard_result.get("ok"):
                    request.session["company_registration_success"] += (
                        f" Oppi Ponto: {onboard_result.get('message')}"
                    )
                    if onboard_result.get("password"):
                        request.session["company_registration_success"] += (
                            f" Senha: {onboard_result['password']}"
                        )
        except Exception:
            pass
        request.session["edit_success"] = request.session.get("company_registration_success", "")
        return RedirectResponse(
            url=_edit_page_url(sheet_row, from_page=from_page, tab="treinamento"),
            status_code=303,
        )
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
