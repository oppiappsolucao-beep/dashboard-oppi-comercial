"""Mapeamento de valores legados para o processo comercial oficial."""

LEGACY_STAGE_MAP = {
    "Primeiro Contato": "Contato",
    "Contato inicial": "Contato",
    "Primeiro contato": "Contato",
    "Prospectando": "Qualificação",
    "Prospecção": "Qualificação",
    "Apresentação": "Reunião",
    "Proposta Enviada": "Proposta",
    "Proposta enviada": "Proposta",
    "Aguardando resposta": "Retorno",
    "Follow-up": "Retorno",
    "Follow up": "Retorno",
    "Sem resposta": "Retorno",
    "Fechamento": "Negociação",
    "Ganho": "Fechado",
    "Perdido": "Negociação",
    "Chamado Whats": "Contato",
    "Conversando": "Qualificação",
    "Retornar": "Retorno",
    "Ligação retornar": "Retorno",
    "Sem Resposta": "Retorno",
    "Não responde": "Retorno",
    "Ligação": "Contato",
    "Ligação - Conversando Whats": "Contato",
}

LEGACY_RESULT_MAP = {
    "Sem resposta": "Cliente não respondeu",
    "Não respondeu": "Cliente não respondeu",
    "Contato realizado": "Cliente respondeu",
    "Tem interesse": "Interesse confirmado",
    "Reunião marcada": "Reunião agendada",
    "Reunião confirmada": "Reunião agendada",
    "Orçamento enviado": "Proposta enviada",
    "Follow-up realizado": "Em análise pelo cliente",
    "Fechado": "Venda fechada",
    "Não tem interesse": "Sem interesse",
    "Lead encerrado": "Sem interesse",
    "Encerrar oportunidade": "Sem interesse",
}

LEGACY_ACTION_MAP = {
    "Primeiro contato": "Fazer primeiro contato",
    "Fazer follow-up": "Fazer acompanhamento da proposta",
    "Follow-up": "Fazer acompanhamento da proposta",
    "Realizar follow-up da proposta": "Fazer acompanhamento da proposta",
    "Ligar agora": "Retornar contato",
    "Retorno": "Retornar contato",
    "Agendar": "Agendar reunião",
    "Agendar retorno": "Reagendar retorno",
    "Enviar orçamento": "Enviar proposta",
    "Cobrar proposta": "Fazer acompanhamento da proposta",
    "Retomar qualificação": "Qualificar lead",
    "Retomar negociação": "Negociar condições",
    "Definir próxima ação": "Definir próximo passo",
    "Encerrar oportunidade": "Encerrar processo comercial",
}

LEGACY_CHANNEL_MAP = {
    "Whats": "WhatsApp",
    "Whatsapp": "WhatsApp",
    "whatsapp": "WhatsApp",
    "Telefone": "Ligação",
    "ligacao": "Ligação",
    "Email": "E-mail",
    "email": "E-mail",
    "Meet": "Reunião online",
    "reuniao": "Reunião online",
    "Tarefa": "Mensagem interna",
    "tarefa": "Mensagem interna",
    "Follow-up": "WhatsApp",
}

LEGACY_STATUS_MAP = {
    "Concluida": "Concluída",
    "concluida": "Concluída",
    "Pendente": "Pendente",
    "pendente": "Pendente",
    "Atrasada": "Atrasada",
    "atrasada": "Atrasada",
    "Cancelada": "Cancelada",
    "cancelada": "Cancelada",
}

LEGACY_OPPORTUNITY_STATUS_MAP = {
    "Aberto": "Aberta",
    "Perdido": "Fechada perdida",
    "Ganho": "Fechada ganha",
    "Encerrado": "Encerrada",
}

MIGRATION_FIELD_LABELS = {
    "stage": "Etapa",
    "stage_override": "Etapa (override)",
    "process_action": "Atividade",
    "title": "Atividade (título)",
    "next_action": "Próxima ação",
    "next_action_description": "Próxima ação (descrição)",
    "result": "Resultado",
    "channel": "Canal",
    "next_action_channel": "Canal da próxima ação",
    "status": "Status",
    "opportunity_status": "Status da oportunidade",
}
