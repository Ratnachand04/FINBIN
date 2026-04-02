param(
    [string]$ModelName = "binfin-mistral-finance"
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$modelfilePath = Join-Path $root "models\active\Modelfile"
if (-not (Test-Path $modelfilePath)) {
    throw "Missing $modelfilePath. Create models/active/Modelfile first."
}

Write-Host "Ensuring Ollama service is running..."
docker compose up -d ollama

Write-Host "Building active model '$ModelName' from /models/active/Modelfile..."
docker compose exec -T ollama sh -lc "ollama create $ModelName -f /models/active/Modelfile"

Write-Host "Model replacement complete. Active Ollama model: $ModelName"
