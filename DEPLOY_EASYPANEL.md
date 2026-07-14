# Deploy no Easypanel — checklist

## 1. Fonte
- Repositório: `oppiappsolucao-beep/dashboard-oppi-comercial`
- Branch: `main`
- Build: **Dockerfile**

## 2. Porta (IMPORTANTE)
- **Porta interna do serviço:** `8501`
- Deve ser a mesma que o app escuta (`PORT=8501`)

## 3. Comando de início
Deixe **VAZIO** (usa o Dockerfile).

**Não use:**
```
uvicorn app.main:app ...
streamlit run app.py ...   # se já usa Dockerfile
sh start.sh                # opcional, Dockerfile já inicia
```

## 4. Variáveis de ambiente
| Variável | Obrigatório | Exemplo |
|----------|-------------|---------|
| APP_SECRET_KEY | Sim | chave-longa-aleatoria |
| DATABASE_URL | Não | vazio = SQLite no container |
| PORT | Não | 8501 (Easypanel pode definir) |

Remova variáveis antigas do FastAPI:
- APP_USERNAME
- APP_PASSWORD
- SESSION_SECRET
- GCP_SERVICE_ACCOUNT_B64 (só se não usar Sheets)

## 5. Após deploy
Acesse: **https://comercial.oppitech.com.br/** (sem `/visao-geral`)

Login (criado automaticamente na 1ª subida):
- Usuário: `oppitech`
- Senha: `100316*`

## 6. Se aparecer "Service is not reachable"
1. Abra **Logs** do container no Easypanel
2. Confirme a porta interna = **8501**
3. Confirme que o build terminou sem erro
4. Faça **Rebuild** sem cache
5. Envie as últimas 30 linhas do log

## 7. PostgreSQL (recomendado produção)
```
DATABASE_URL=postgresql+psycopg2://usuario:senha@host:5432/oppi_crm
```
