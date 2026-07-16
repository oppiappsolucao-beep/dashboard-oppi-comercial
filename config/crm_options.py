"""Opções oficiais do processo comercial Oppi CRM — fonte única de verdade."""

PIPELINE_STAGE_OPTIONS = [
    "Novo Lead",
    "Contato",
    "Qualificação",
    "Reunião",
    "Proposta",
    "Retorno",
    "Negociação",
    "Fechado",
]

PIPELINE_STAGE_SLA = {
    "Novo Lead": {"label": "Até 1 hora", "max_hours": 1},
    "Contato": {"label": "No mesmo dia", "same_day": True},
    "Qualificação": {"label": "1 a 3 dias", "max_days": 3},
    "Reunião": {"label": "Até 7 dias", "max_days": 7},
    "Proposta": {"label": "Até 24 horas", "max_hours": 24},
    "Retorno": {"label": "2 dias", "max_days": 2},
    "Negociação": {"label": "3 a 7 dias", "max_days": 7},
    "Fechado": {"label": "Processo concluído", "completed": True},
}

PROCESS_GUIDE = [(stage, PIPELINE_STAGE_SLA[stage]["label"]) for stage in PIPELINE_STAGE_OPTIONS]

PROCESS_ACTION_OPTIONS = [
    "Fazer primeiro contato",
    "Verificar dados do lead",
    "Atribuir responsável",
    "Realizar contato",
    "Retornar contato",
    "Enviar apresentação inicial",
    "Confirmar interesse",
    "Qualificar lead",
    "Entender necessidade",
    "Identificar decisor",
    "Validar orçamento",
    "Definir próximo passo",
    "Agendar reunião",
    "Confirmar reunião",
    "Realizar reunião",
    "Reagendar reunião",
    "Criar proposta",
    "Revisar proposta",
    "Enviar proposta",
    "Ajustar proposta",
    "Fazer acompanhamento da proposta",
    "Verificar decisão",
    "Cobrar posicionamento",
    "Reagendar retorno",
    "Negociar condições",
    "Ajustar valores",
    "Ajustar escopo",
    "Definir forma de pagamento",
    "Enviar contrato",
    "Confirmar assinatura",
    "Confirmar pagamento",
    "Iniciar implantação",
    "Encerrar processo comercial",
    "Confirmar fechamento",
]

NEXT_ACTION_OPTIONS = [
    "Fazer primeiro contato",
    "Qualificar lead",
    "Agendar reunião",
    "Criar proposta",
    "Retornar contato",
    "Negociar condições",
    "Confirmar fechamento",
]

NEXT_ACTION_BY_STAGE = {
    "Novo Lead": "Fazer primeiro contato",
    "Contato": "Qualificar lead",
    "Qualificação": "Agendar reunião",
    "Reunião": "Criar proposta",
    "Proposta": "Retornar contato",
    "Retorno": "Negociar condições",
    "Negociação": "Confirmar fechamento",
    "Fechado": "Confirmar fechamento",
}

PROCESS_ACTIONS_BY_STAGE = {
    "Novo Lead": [
        "Fazer primeiro contato",
        "Verificar dados do lead",
        "Atribuir responsável",
    ],
    "Contato": [
        "Realizar contato",
        "Retornar contato",
        "Enviar apresentação inicial",
        "Confirmar interesse",
        "Qualificar lead",
        "Definir próximo passo",
    ],
    "Qualificação": [
        "Qualificar lead",
        "Entender necessidade",
        "Identificar decisor",
        "Validar orçamento",
        "Definir próximo passo",
        "Agendar reunião",
    ],
    "Reunião": [
        "Agendar reunião",
        "Confirmar reunião",
        "Realizar reunião",
        "Reagendar reunião",
        "Criar proposta",
    ],
    "Proposta": [
        "Criar proposta",
        "Revisar proposta",
        "Enviar proposta",
        "Ajustar proposta",
        "Fazer acompanhamento da proposta",
        "Reagendar retorno",
    ],
    "Retorno": [
        "Retornar contato",
        "Fazer acompanhamento da proposta",
        "Verificar decisão",
        "Cobrar posicionamento",
        "Reagendar retorno",
        "Negociar condições",
    ],
    "Negociação": [
        "Negociar condições",
        "Ajustar valores",
        "Ajustar escopo",
        "Definir forma de pagamento",
        "Enviar contrato",
        "Confirmar fechamento",
        "Confirmar assinatura",
    ],
    "Fechado": [
        "Confirmar assinatura",
        "Confirmar pagamento",
        "Confirmar fechamento",
        "Iniciar implantação",
        "Encerrar processo comercial",
    ],
}

ACTIVITY_RESULT_OPTIONS = [
    "Selecione",
    "Cliente respondeu",
    "Cliente não respondeu",
    "Pediu retorno",
    "Avançou de etapa",
    "Sem interesse",
    "Contato inválido",
]

CHANNEL_OPTIONS = [
    "WhatsApp",
    "Ligação",
    "E-mail",
    "Reunião online",
    "Reunião presencial",
    "Mensagem interna",
    "Outro",
]

ACTIVITY_STATUS_OPTIONS = [
    "Pendente",
    "Em andamento",
    "Concluída",
    "Atrasada",
    "Reagendada",
    "Cancelada",
]

ACTIVITY_STATUS_KEYS = {
    "Pendente": "pendente",
    "Em andamento": "em_andamento",
    "Concluída": "concluida",
    "Atrasada": "atrasada",
    "Reagendada": "reagendada",
    "Cancelada": "cancelada",
}

ACTIVITY_STATUS_LABELS = {value: key for key, value in ACTIVITY_STATUS_KEYS.items()}

SELECTABLE_ACTIVITY_STATUS_KEYS = [
    ("pendente", "Pendente"),
    ("em_andamento", "Em andamento"),
    ("concluida", "Concluída"),
    ("reagendada", "Reagendada"),
    ("cancelada", "Cancelada"),
]

NEW_ACTIVITY_STATUS_KEYS = [
    ("pendente", "Pendente"),
    ("em_andamento", "Em andamento"),
    ("concluida", "Concluída"),
    ("reagendada", "Reagendada"),
    ("cancelada", "Cancelada"),
]

ACTIVITY_TYPE_OPTIONS = [
    "Contato",
    "Qualificação",
    "Retorno",
    "Reunião",
    "Proposta",
    "Negociação",
    "Contrato",
    "Pagamento",
    "Implantação",
    "Tarefa interna",
    "Outro",
]

ACTIVITY_TYPE_DEFAULT_ACTION = {
    "Contato": "Fazer primeiro contato",
    "Qualificação": "Qualificar lead",
    "Retorno": "Retornar contato",
    "Reunião": "Agendar reunião",
    "Proposta": "Criar proposta",
    "Negociação": "Negociar condições",
    "Contrato": "Enviar contrato",
    "Pagamento": "Confirmar pagamento",
    "Implantação": "Iniciar implantação",
    "Tarefa interna": "Definir próximo passo",
    "Outro": "Definir próximo passo",
}

ACTIVITY_TYPE_STAGE_HINT = {
    "Contato": "Contato",
    "Qualificação": "Qualificação",
    "Retorno": "Retorno",
    "Reunião": "Reunião",
    "Proposta": "Proposta",
    "Negociação": "Negociação",
    "Contrato": "Negociação",
    "Pagamento": "Fechado",
    "Implantação": "Fechado",
    "Tarefa interna": "Contato",
    "Outro": "Contato",
}

PRIORITY_OPTIONS = [
    "Baixa",
    "Média",
    "Alta",
    "Crítica",
]

PRIORITY_SCORE_VALUES = {
    "Baixa": 10,
    "Média": 20,
    "Alta": 50,
    "Crítica": 100,
}

DATE_QUICK_SUGGESTIONS = [
    ("Hoje", 0),
    ("Amanhã", 1),
    ("Próximo dia útil", 1),
    ("Em 2 dias", 2),
    ("Em 3 dias", 3),
    ("Em 7 dias", 7),
]

OPPORTUNITY_STATUS_OPTIONS = [
    "Aberta",
    "Fechada ganha",
    "Fechada perdida",
    "Encerrada",
]

LOST_REASON_OPTIONS = [
    "Sem orçamento",
    "Preço elevado",
    "Escolheu concorrente",
    "Projeto adiado",
    "Sem urgência",
    "Não é o decisor",
    "Serviço fora do perfil",
    "Não respondeu",
    "Dados de contato inválidos",
    "Outro",
]

CLOSED_RESULTS = {"Sem interesse", "Contato inválido"}
NO_NEXT_ACTION_RESULTS = CLOSED_RESULTS | {"Encerrar processo comercial"}

PIPELINE_STAGE_COLORS = {
    "Novo Lead": "#8B5CF6",
    "Contato": "#EC4899",
    "Qualificação": "#3B82F6",
    "Reunião": "#10B981",
    "Proposta": "#F59E0B",
    "Retorno": "#A855F7",
    "Negociação": "#EA580C",
    "Fechado": "#22C55E",
}

PIPELINE_STAGE_BADGE = {
    "Novo Lead": "novo-lead",
    "Contato": "contato",
    "Qualificação": "qualificacao",
    "Reunião": "reuniao",
    "Proposta": "proposta",
    "Retorno": "retorno",
    "Negociação": "negociacao",
    "Fechado": "fechado",
}

PIPELINE_STAGE_SHEET_STATUSES = {
    "Novo Lead": ["Novo Lead"],
    "Contato": ["Chamado Whats", "Ligação", "Ligação - Conversando Whats", "Sem Whatsapp"],
    "Qualificação": ["Conversando"],
    "Reunião": ["Reunião"],
    "Proposta": ["Proposta"],
    "Retorno": ["Retornar", "Ligação retornar", "Sem Resposta", "Não responde"],
    "Negociação": [],
    "Fechado": ["Fechado"],
}

SHEET_STATUS_TO_PIPELINE_STAGE = {
    "Novo Lead": "Novo Lead",
    "Chamado Whats": "Contato",
    "Ligação": "Contato",
    "Ligação - Conversando Whats": "Contato",
    "Sem Whatsapp": "Contato",
    "Conversando": "Qualificação",
    "Reunião": "Reunião",
    "Proposta": "Proposta",
    "Retornar": "Retorno",
    "Ligação retornar": "Retorno",
    "Sem Resposta": "Retorno",
    "Não responde": "Retorno",
    "Fechado": "Fechado",
    "Sem interesse": "Negociação",
}

OPEN_OPPORTUNITY_STATUSES = {"Aberta"}

COMPLETED_SHEET_STATUSES = {"Fechado", "Sem interesse"}

FIRST_CONTACT_SHEET_STATUSES = {
    "Chamado Whats",
    "Ligação - Conversando Whats",
    "Ligação",
    "Conversando",
    "Reunião",
    "Proposta",
    "Retornar",
    "Ligação retornar",
    "Sem Resposta",
}

CHANNEL_KEY_TO_LABEL = {
    "whatsapp": "WhatsApp",
    "ligacao": "Ligação",
    "email": "E-mail",
    "reuniao": "Reunião online",
    "reuniao_online": "Reunião online",
    "reuniao_presencial": "Reunião presencial",
    "tarefa": "Mensagem interna",
    "outro": "Outro",
}

CHANNEL_LABEL_TO_KEY = {
    "WhatsApp": "whatsapp",
    "Ligação": "ligacao",
    "E-mail": "email",
    "Reunião online": "reuniao",
    "Reunião presencial": "reuniao_presencial",
    "Mensagem interna": "tarefa",
    "Outro": "outro",
}

CHANNEL_CLASS = {
    "WhatsApp": "whatsapp",
    "Ligação": "ligacao",
    "E-mail": "email",
    "Reunião online": "reuniao",
    "Reunião presencial": "reuniao",
    "Mensagem interna": "tarefa",
    "Outro": "tarefa",
}

OVERVIEW_ACTION_TO_PROCESS = {
    "Fazer primeiro contato": "Fazer primeiro contato",
    "Definir próximo passo": "Definir próximo passo",
    "Definir próxima ação": "Definir próximo passo",
    "Retomar qualificação": "Qualificar lead",
    "Retomar negociação": "Negociar condições",
    "Realizar follow-up da proposta": "Fazer acompanhamento da proposta",
    "Fazer follow-up": "Fazer acompanhamento da proposta",
    "Criar proposta": "Criar proposta",
    "Confirmar reunião": "Confirmar reunião",
    "Agendar retorno": "Reagendar retorno",
    "Agendar reunião": "Agendar reunião",
    "Retornar contato": "Retornar contato",
    "Confirmar fechamento": "Confirmar fechamento",
}

ACTION_DESCRIPTIONS = {
    "Fazer primeiro contato": "Entrar em contato inicial com o lead.",
    "Verificar dados do lead": "Validar telefone, e-mail e informações cadastrais.",
    "Atribuir responsável": "Definir o vendedor responsável pelo lead.",
    "Realizar contato": "Executar o contato comercial previsto.",
    "Retornar contato": "Retomar conversa conforme combinado.",
    "Enviar apresentação inicial": "Enviar material introdutório da solução.",
    "Confirmar interesse": "Validar interesse comercial do lead.",
    "Qualificar lead": "Entender perfil, necessidade e fit comercial.",
    "Entender necessidade": "Mapear dor, contexto e prioridade do cliente.",
    "Identificar decisor": "Confirmar quem decide a compra.",
    "Validar orçamento": "Verificar capacidade financeira e expectativa de valor.",
    "Definir próximo passo": "Registrar o próximo passo comercial acordado.",
    "Agendar reunião": "Marcar reunião comercial com o decisor.",
    "Confirmar reunião": "Confirmar presença antes da reunião.",
    "Realizar reunião": "Conduzir apresentação ou alinhamento.",
    "Reagendar reunião": "Remarcar reunião não realizada.",
    "Criar proposta": "Montar proposta comercial.",
    "Revisar proposta": "Revisar conteúdo e condições da proposta.",
    "Enviar proposta": "Enviar proposta formal ao cliente.",
    "Ajustar proposta": "Ajustar proposta conforme feedback do cliente.",
    "Fazer acompanhamento da proposta": "Acompanhar retorno da proposta enviada.",
    "Verificar decisão": "Verificar posicionamento do cliente sobre a proposta.",
    "Cobrar posicionamento": "Solicitar retorno formal do cliente.",
    "Reagendar retorno": "Reagendar retorno comercial.",
    "Negociar condições": "Tratar objeções e condições comerciais.",
    "Ajustar valores": "Ajustar valores da proposta ou contrato.",
    "Ajustar escopo": "Ajustar escopo da solução proposta.",
    "Definir forma de pagamento": "Alinhar condições de pagamento.",
    "Enviar contrato": "Enviar documentação para assinatura.",
    "Confirmar assinatura": "Validar assinatura do contrato.",
    "Confirmar pagamento": "Confirmar recebimento ou pagamento.",
    "Iniciar implantação": "Iniciar onboarding do cliente.",
    "Encerrar processo comercial": "Encerrar oportunidade comercial.",
    "Confirmar fechamento": "Validar assinatura, pagamento e conclusão comercial.",
}

NEXT_ACTION_DESCRIPTIONS = {
    "Fazer primeiro contato": "Entrar em contato inicial com o lead.",
    "Qualificar lead": "Entender perfil, necessidade e fit comercial.",
    "Agendar reunião": "Marcar reunião comercial com o decisor.",
    "Criar proposta": "Montar e enviar proposta comercial.",
    "Retornar contato": "Retomar conversa conforme combinado.",
    "Negociar condições": "Tratar objeções e condições comerciais.",
    "Confirmar fechamento": "Confirmar assinatura, pagamento ou conclusão da venda.",
}

RESULT_SUGGESTIONS = {
    "Cliente respondeu": {
        "stage": "Contato",
        "next_action": "Qualificar lead",
        "days": 1,
        "channel": "WhatsApp",
    },
    "Cliente não respondeu": {
        "stage": "",
        "next_action": "Retornar contato",
        "days": 1,
        "channel": "WhatsApp",
    },
    "Pediu retorno": {
        "stage": "Retorno",
        "next_action": "Retornar contato",
        "days": 0,
        "channel": "WhatsApp",
        "require_schedule": True,
    },
    "Avançou de etapa": {
        "advance_stage": True,
        "next_action": "Qualificar lead",
        "days": 1,
        "channel": "WhatsApp",
    },
    "Sem interesse": {
        "stage": "",
        "next_action": "Encerrar processo comercial",
        "days": 0,
        "channel": "Mensagem interna",
        "opportunity_status": "Fechada perdida",
        "require_reason": True,
    },
    "Contato inválido": {
        "stage": "",
        "next_action": "Encerrar processo comercial",
        "days": 0,
        "channel": "Mensagem interna",
        "opportunity_status": "Encerrada",
    },
}

VALIDATION_ERROR_MESSAGE = (
    "A opção selecionada não pertence ao processo comercial atual. "
    "Atualize o campo antes de salvar."
)

OVERVIEW_FUNNEL_STAGES = [
    (stage, PIPELINE_STAGE_SHEET_STATUSES.get(stage, []), PIPELINE_STAGE_COLORS[stage])
    for stage in PIPELINE_STAGE_OPTIONS
]
