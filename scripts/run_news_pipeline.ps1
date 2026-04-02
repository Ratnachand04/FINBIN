param(
    [string]$DbUser = "binfin",
    [string]$DbName = "binfin",
    [string]$DbPassword = "binfin",
    [switch]$SkipCollection
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$schemaPath = Join-Path $repoRoot "database\schema.sql"
$collectorPath = Join-Path $repoRoot "scripts\pull_news_and_link_market_data.py"

if (-not (Test-Path $schemaPath)) {
    throw "Schema file not found at $schemaPath"
}

if (-not (Test-Path $collectorPath)) {
    throw "Collector script not found at $collectorPath"
}

Write-Host "[1/4] Starting postgres and redis containers..."
docker compose -f (Join-Path $repoRoot "docker-compose.yml") up -d postgres redis | Out-Host

Write-Host "[2/4] Applying TimescaleDB schema from database/schema.sql..."
Get-Content -Path $schemaPath | docker compose -f (Join-Path $repoRoot "docker-compose.yml") exec -T -e PGPASSWORD=$DbPassword postgres psql -v ON_ERROR_STOP=1 -U $DbUser -d $DbName | Out-Host

if (-not $SkipCollection) {
    Write-Host "[3/4] Running news collection and linking pipeline..."
    Push-Location $repoRoot
    try {
        python .\scripts\pull_news_and_link_market_data.py
    }
    finally {
        Pop-Location
    }
}

Write-Host "[4/4] Verifying news row count..."
docker compose -f (Join-Path $repoRoot "docker-compose.yml") exec -T -e PGPASSWORD=$DbPassword postgres psql -U $DbUser -d $DbName -c "SELECT COUNT(*) AS news_articles_count FROM news_articles;" | Out-Host

Write-Host "Done."
