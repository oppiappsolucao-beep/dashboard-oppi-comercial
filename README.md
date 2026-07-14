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

1. Crie um serviço **App** apontando para este repositório (branch `main`)
2. Use o `Dockerfile` do projeto (não use comando customizado de Streamlit)
3. Configure as variáveis de ambiente listadas acima
4. Em **Domínio e Proxy**, a porta alvo deve ser **`8501`** (padrão herdado do Streamlit) ou a definida em `PORT`
5. Em **Deploy**, deixe **Comando** e **Argumentos** vazios (o `Dockerfile` inicia sozinho)
6. Clique em **Implantar**

### Se aparecer "Service is not reachable"

1. **Logs** — procure erro de variável ausente ou `streamlit run app.py` (comando antigo)
2. **Deploy → Comando** — apague qualquer comando customizado (ex.: `streamlit run app.py`)
3. **Domínio e Proxy → Porta** — confira se é `8501` (ou a mesma de `PORT`)
4. Teste `https://seu-dominio/health` — deve retornar `{"status":"ok"}`

### Variáveis obrigatórias no Easypanel

- `APP_USERNAME`, `APP_PASSWORD`
- `GCP_SERVICE_ACCOUNT_B64` (ou variáveis `GOOGLE_*` separadas)
- `SESSION_SECRET` (opcional — usa `APP_PASSWORD` como fallback)
- `PORT` (injetada automaticamente pelo Easypanel)

## Estrutura

- `app/main.py` — aplicação FastAPI
- `app/routers/` — rotas (auth, visão geral, cadastro, contratos, precificação)
- `app/services/` — lógica de negócio e integração Google Sheets
- `app/templates/` — templates Jinja2
- `app/static/` — CSS e JS
