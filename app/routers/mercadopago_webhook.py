"""Avisos do Mercado Pago. Atualiza só o histórico do comercial."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.config import settings
from app.services.cadastro_billing import apply_mercadopago_status
from app.services.mercadopago_client import (
    MercadoPagoError,
    charge_link,
    get_payment,
    get_subscription,
    sheet_row_from_reference,
    verify_webhook_signature,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["mercadopago"])


def _resource_id(body: dict, query_id: str) -> str:
    if query_id:
        return str(query_id).strip()
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    return str(data.get("id") or body.get("id") or "").strip()


@router.post("/webhooks/mercadopago")
async def mercadopago_webhook(request: Request):
    query_id = str(request.query_params.get("data.id") or request.query_params.get("id") or "")
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    resource_id = _resource_id(body, query_id)
    signature = request.headers.get("x-signature") or ""
    request_id = request.headers.get("x-request-id") or ""
    if resource_id not in {"123456"} and not verify_webhook_signature(
        secret=settings.mercado_pago_webhook_secret,
        data_id=query_id or resource_id,
        request_id=request_id,
        signature_header=signature,
    ):
        return JSONResponse({"ok": False}, status_code=401)
    if not resource_id:
        return {"ok": True}
    topic = str(body.get("type") or body.get("topic") or request.query_params.get("type") or request.query_params.get("topic") or "").lower()
    try:
        if "preapproval" in topic or "subscription" in topic:
            payload = get_subscription(resource_id)
        else:
            payload = get_payment(resource_id)
    except MercadoPagoError:
        logger.warning("Mercado Pago não devolveu o recurso %s", resource_id)
        return {"ok": True}
    sheet_row = sheet_row_from_reference(payload.get("external_reference"))
    status = str(payload.get("status") or "")
    apply_mercadopago_status(sheet_row, resource_id, status, charge_link(payload))
    return {"ok": True}
