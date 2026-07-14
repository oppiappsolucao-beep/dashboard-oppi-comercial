# Executa o dashboard localmente no Windows (PowerShell)
# Uso: .\run.ps1

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

if (-not (Test-Path "app\main.py")) {
    Write-Host "Pasta incorreta. Execute este script dentro de:" -ForegroundColor Red
    Write-Host "  C:\Users\orchi\OneDrive\Documentos\GitHub\dashboard-oppi-comercial" -ForegroundColor Yellow
    Write-Host "A pasta ' dashboard-oppi-comercial' (com espaco) nao contem o codigo." -ForegroundColor Yellow
    exit 1
}

if (-not (Test-Path ".env")) {
    Write-Host "Arquivo .env nao encontrado." -ForegroundColor Yellow
    Write-Host "Copie .env.example para .env e preencha as variaveis:" -ForegroundColor Yellow
    Write-Host "  Copy-Item .env.example .env" -ForegroundColor Cyan
    exit 1
}

Get-Content ".env" | ForEach-Object {
    if ($_ -match '^\s*([^#][^=]+)=(.*)$') {
        $name = $matches[1].Trim()
        $value = $matches[2].Trim()
        if ($name) {
            Set-Item -Path "env:$name" -Value $value
        }
    }
}

$required = @("APP_USERNAME", "APP_PASSWORD", "SESSION_SECRET", "GCP_SERVICE_ACCOUNT_B64")
foreach ($var in $required) {
    if (-not (Get-Item "env:$var" -ErrorAction SilentlyContinue)) {
        Write-Host "Variavel obrigatoria ausente no .env: $var" -ForegroundColor Red
        exit 1
    }
}

Write-Host "Instalando dependencias..." -ForegroundColor Green
py -m pip install -r requirements.txt

Write-Host "Iniciando servidor em http://localhost:8000" -ForegroundColor Green
py -m uvicorn app.main:app --reload --port 8000
