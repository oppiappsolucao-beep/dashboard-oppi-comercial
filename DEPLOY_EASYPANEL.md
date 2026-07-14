# Deploy no Easypanel — Dashboard Oppi Comercial (FastAPI)

## Porta interna
**8501**

## Comando de início
Deixe **VAZIO** (usa Dockerfile) ou:
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8501 --proxy-headers
```

**NÃO use** `streamlit run app.py` neste deploy.

## Variáveis OBRIGATÓRIAS no Easypanel

| Variável | Valor |
|----------|--------|
| APP_USERNAME | oppitech |
| APP_PASSWORD | 100316* |
| GCP_SERVICE_ACCOUNT_B64 | (sua credencial Google Sheets em base64) |

Opcional:
| SESSION_SECRET | chave forte (se vazio, usa APP_PASSWORD) |

## URLs
- https://comercial.oppitech.com.br/
- https://comercial.oppitech.com.br/visao-geral
- https://comercial.oppitech.com.br/funil-de-vendas
- https://comercial.oppitech.com.br/login

## Login
- Usuário: **oppitech**
- Senha: **100316***

## Após alterar variáveis
1. Salvar variáveis
2. **Rebuild** do serviço
3. Aguardar status verde
4. Testar `/health` → deve retornar `{"status":"ok"}`

## Se "Service is not reachable"
1. Porta interna = **8501**
2. Comando de início vazio ou uvicorn (não streamlit)
3. APP_USERNAME e APP_PASSWORD definidos
4. Ver logs do container
