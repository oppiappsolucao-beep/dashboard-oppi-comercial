COLORS = {
    "bg": "#F7F8FC",
    "card": "#FFFFFF",
    "sidebar": "#10122E",
    "primary": "#6D28D9",
    "primary_light": "#8B5CF6",
    "pink": "#EC4899",
    "blue": "#2563EB",
    "green": "#16A34A",
    "orange": "#F59E0B",
    "red": "#EF4444",
    "text": "#111827",
    "text_secondary": "#64748B",
    "border": "#E5E7EB",
}

STAGE_COLORS = [
    "#6D28D9",
    "#EC4899",
    "#2563EB",
    "#16A34A",
    "#F59E0B",
    "#8B5CF6",
    "#22C55E",
    "#EF4444",
]

DEFAULT_PIPELINE_STAGES = [
    ("Novo Lead", 1, "#8B5CF6", 10),
    ("Contato", 2, "#EC4899", 20),
    ("Qualificação", 3, "#3B82F6", 35),
    ("Reunião", 4, "#10B981", 50),
    ("Proposta", 5, "#F59E0B", 65),
    ("Retorno", 6, "#A855F7", 75),
    ("Negociação", 7, "#EA580C", 85),
    ("Fechado", 8, "#22C55E", 100),
]

ROLES = ["Administrador", "Gestor", "Vendedor", "Financeiro", "Analista"]
