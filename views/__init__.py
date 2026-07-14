from views.activities import render as render_activities
from views.dashboard import render as render_dashboard
from views.goals_reports import render as render_goals_reports
from views.lead_details import render as render_lead_details
from views.leads_companies import render as render_leads_companies
from views.pipeline import render as render_pipeline
from views.proposals import render as render_proposals
from views.settings_page import render as render_settings

PAGE_RENDERERS = {
    "Visão Geral": render_dashboard,
    "Funil de Vendas": render_pipeline,
    "Leads e Empresas": render_leads_companies,
    "Atividades": render_activities,
    "Propostas": render_proposals,
    "Metas e Relatórios": render_goals_reports,
    "Configurações": render_settings,
}

__all__ = ["PAGE_RENDERERS", "render_lead_details"]
