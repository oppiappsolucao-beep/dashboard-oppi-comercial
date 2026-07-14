# Dashboard Oppi Comercial

Dashboard comercial da Oppi Tech em **FastAPI + Jinja2 + HTMX**, com deploy no **Easypanel**.

## Requisitos

- Python 3.12+
- Conta de serviço Google com acesso à planilha

## Variáveis de ambiente (obrigatórias)

| Variável | Descrição |
|----------|-----------|
| `GCP_SERVICE_ACCOUNT_B64` | JSON da service account em Base64 |
| `APP_USERNAME` | Usuário de login |
| `APP_PASSWORD` | Senha de login |
| `SESSION_SECRET` | Chave secreta para assinar cookies de sessão |
| `PORT` | Porta HTTP (Easypanel injeta automaticamente) |

## Executar localmente

### Windows (PowerShell)

No seu PC, `pip` e `uvicorn` não ficam no PATH diretamente. Use o launcher `py`:

```powershell
# 1. Instalar dependências
py -m pip install -r requirements.txt

# 2. Criar e preencher o .env
Copy-Item .env.example .env
# Edite .env com APP_USERNAME, APP_PASSWORD, SESSION_SECRET e GCP_SERVICE_ACCOUNT_B64

# 3. Subir o servidor
.\run.ps1
```

Ou manualmente:

```powershell
py -m pip install -r requirements.txt
$env:APP_USERNAME="seu_usuario"
$env:APP_PASSWORD="sua_senha"
$env:SESSION_SECRET="uma_chave_secreta_longa"
$env:GCP_SERVICE_ACCOUNT_B64="..."
py -m uvicorn app.main:app --reload --port 8000
```

Atalhos:
- `.\setup.ps1` — instala dependências e cria `.env`
- `.\run.ps1` — lê `.env` e inicia o servidor

### Linux / macOS

```bash
pip install -r requirements.txt

export APP_USERNAME=seu_usuario
export APP_PASSWORD=sua_senha
export SESSION_SECRET=uma_chave_secreta_longa
export GCP_SERVICE_ACCOUNT_B64=...

uvicorn app.main:app --reload --port 8000
```

Acesse: http://localhost:8000

## Deploy no Easypanel

1. Crie um serviço **App** apontando para este repositório
2. Use o `Dockerfile` do projeto
3. Configure as variáveis de ambiente listadas acima
4. Porta interna: `8000` (ou a definida em `PORT`)
5. Comando de start (se não usar Docker): `uvicorn app.main:app --host 0.0.0.0 --port $PORT`

## Estrutura

- `app/main.py` — aplicação FastAPI
- `app/routers/` — rotas (auth, visão geral, cadastro, contratos, precificação)
- `app/services/` — lógica de negócio e integração Google Sheets
- `app/templates/` — templates Jinja2
- `app/static/` — CSS e JS
