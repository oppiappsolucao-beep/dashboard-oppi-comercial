# Oppi CRM Comercial

Sistema SaaS multiempresa para gestão comercial: leads, funil, atividades, propostas com IA, metas, relatórios e integrações.

## Funcionalidades

- **Multiempresa (tenant_id)** com isolamento de dados por empresa
- **Autenticação** com hash de senha (bcrypt/passlib)
- **Perfis**: Administrador, Gestor, Vendedor, Financeiro, Analista
- **Visão Geral** com KPIs, funil, ações do dia e oportunidades quentes
- **Funil de Vendas** analítico + Kanban com movimentação de etapas
- **Leads e Empresas** com cadastro e filtros
- **Atividades** com controle de pendências e atrasos
- **Propostas** com formulário tradicional, IA (OpenAI) e geração de PDF
- **Metas e Relatórios** por vendedor e período
- **Configurações** de empresa, serviços, pipeline e integrações
- **Integrações preparadas**: WhatsApp, n8n, Asaas, ZapSign, Google Sheets, IA

## Tecnologias

- Python 3.11+
- Streamlit
- PostgreSQL / SQLite
- SQLAlchemy
- Pandas / Plotly
- ReportLab

## Estrutura

```
app.py                 # Entrada Streamlit
config/                # Settings e tema
database/              # Models, conexão, repositories, seed
auth/                  # Login, senhas, permissões
pages/                 # Telas do CRM
services/              # Regras de negócio e integrações
components/            # UI reutilizável
utils/                 # Formatadores e validadores
assets/styles.css      # Identidade visual
generated/proposals/   # PDFs gerados
```

## Instalação local

```bash
python -m venv .venv
.venv\Scripts\activate   # Windows
pip install -r requirements.txt
copy .env.example .env
python -m database.seed
streamlit run app.py
```

Acesse: http://localhost:8501

## Usuário inicial (seed)

Após `python -m database.seed`:

- **Usuário:** oppitech
- **Senha:** 100316*

> O seed **não** roda automaticamente em produção.

## Docker

```bash
docker compose up --build
```

Comando de inicialização:

```bash
streamlit run app.py --server.address=0.0.0.0 --server.port=8501
```

## EasyPanel / Hostinger

1. Conecte o repositório GitHub
2. Use o `Dockerfile` do projeto
3. Porta: **8501**
4. Configure as variáveis do `.env.example`
5. Use PostgreSQL em produção (`DATABASE_URL`)
6. Defina `APP_SECRET_KEY` forte
7. **Não** habilite `RUN_SEED_ON_STARTUP` em produção

## Variáveis de ambiente

Veja `.env.example` para a lista completa.

Principais:

| Variável | Descrição |
|----------|-----------|
| DATABASE_URL | PostgreSQL ou SQLite |
| APP_SECRET_KEY | Chave da sessão |
| OPENAI_API_KEY | Agente de propostas |
| RUN_SEED_ON_STARTUP | Seed automático (dev only) |

## Segurança

- Senhas com hash bcrypt
- Isolamento por `tenant_id` em todas as consultas
- Credenciais apenas via variáveis de ambiente
- Sessão com expiração configurável

## Integrações

Cada integração possui serviço adaptador em `services/`:

- `whatsapp_service.py`
- `n8n_service.py`
- `asaas_service.py`
- `zapsign_service.py`
- `ai_service.py`

Configure as variáveis e teste em **Configurações > Integrações**.

## Migração

Este repositório foi reconstruído como **Oppi CRM Comercial** em Streamlit. A estrutura FastAPI anterior (`app/`) permanece no repositório apenas como legado; o deploy deve usar `app.py` (Streamlit).
