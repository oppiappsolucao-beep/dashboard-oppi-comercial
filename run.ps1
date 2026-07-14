# Oppi CRM Comercial - execução local
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
python -m database.seed
streamlit run app.py --server.port 8501
