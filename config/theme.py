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
    ("Contato", 1, "#EC4899", 20),
    ("Qualificação", 2, "#3B82F6", 35),
    ("Reunião", 3, "#10B981", 50),
    ("Proposta", 4, "#F59E0B", 65),
    ("Retorno", 5, "#A855F7", 75),
    ("Negociação", 6, "#EA580C", 85),
    ("Fechado", 7, "#22C55E", 100),
    ("Perdido", 8, "#EF4444", 0),
]

ROLES = ["Administrador", "Gestor", "Vendedor", "Financeiro", "Analista"]
