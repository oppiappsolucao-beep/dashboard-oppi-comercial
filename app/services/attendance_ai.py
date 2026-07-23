"""IA de atendimento — estrutura; prompt será configurado depois."""
from __future__ import annotations

from app.config import settings
from app.services.attendances_storage import AI_MODE_ON
from app.services.legacy_core import normalize_text


def should_reply(*, ai_mode: str) -> bool:
    if not settings.ai_attendance_enabled:
        return False
    return normalize_text(ai_mode) == AI_MODE_ON


def generate_reply(
    *,
    conversation: dict,
    inbound_text: str,
    history: list[dict] | None = None,
) -> str | None:
    """
    Retorna texto de resposta da IA, ou None se ainda não houver prompt/provider.

    Quando o prompt for definido (AI_ATTENDANCE_PROMPT / settings.ai_attendance_prompt),
    esta função será o ponto de integração com o modelo.
    """
    if not should_reply(ai_mode=conversation.get("ai_mode", AI_MODE_ON)):
        return None
    prompt = normalize_text(settings.ai_attendance_prompt)
    if not prompt:
        return None
    # Placeholder até o prompt/provider serem definidos pelo time.
    _ = (inbound_text, history)
    return None
