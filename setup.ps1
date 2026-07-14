# Instala dependencias no Windows (PowerShell)
# Uso: .\setup.ps1

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

Write-Host "Instalando dependencias com py -m pip..." -ForegroundColor Green
py -m pip install -r requirements.txt

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Arquivo .env criado a partir de .env.example" -ForegroundColor Yellow
    Write-Host "Edite .env e preencha APP_USERNAME, APP_PASSWORD, SESSION_SECRET e GCP_SERVICE_ACCOUNT_B64" -ForegroundColor Yellow
}

Write-Host "Pronto. Execute: .\run.ps1" -ForegroundColor Green
