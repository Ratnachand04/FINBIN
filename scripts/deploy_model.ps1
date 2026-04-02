param(
    [switch]$Train,
    [switch]$FineTune,
    [string]$TrainSymbols = "BTCUSDT,ETHUSDT,DOGEUSDT",
    [string]$TrainInterval = "15m",
    [int]$TrainRows = 6000,
    [int]$SentimentSamples = 30,
    [string]$AdapterName = "binfin-mistral-qlora",
    [string]$OllamaFineTunedModelName = "binfin-mistral-finance",
    [int]$FineTuneDatasetLimit = 15000,
    [double]$FineTuneEpochs = 1.0,
    [double]$FineTuneLearningRate = 0.0002,
    [int]$FineTuneBatchSize = 1,
    [int]$FineTuneGradAccum = 16,
    [ValidateSet("gpu-qlora", "cpu-lora")]
    [string]$FineTuneTrainerMode = "gpu-qlora"
)

$ErrorActionPreference = "Stop"

$modelBootstrapName = if ($env:OLLAMA_MODEL) { $env:OLLAMA_MODEL } else { "mistral:7b-instruct-q4_K_M" }

function Test-CommandAvailable {
    param([string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command not found: $Name"
    }
}

function Wait-ForHttp {
    param(
        [string]$Url,
        [int]$Attempts = 60,
        [int]$DelaySeconds = 2
    )

    for ($i = 1; $i -le $Attempts; $i++) {
        try {
            $null = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
            return $true
        }
        catch {
            Start-Sleep -Seconds $DelaySeconds
        }
    }

    return $false
}

function Initialize-OllamaModel {
    param([string]$ModelName)

    $activeModelfile = "/models/active/Modelfile"
    Write-Host "Preparing Ollama model: $ModelName"
    docker compose exec -T ollama sh -lc "test -f $activeModelfile"
    if ($LASTEXITCODE -eq 0) {
        Write-Host "Found /models/active/Modelfile. Creating replaceable model '$ModelName'..."
        docker compose exec -T ollama sh -lc "ollama create $ModelName -f $activeModelfile"
    }
    else {
        Write-Host "No /models/active/Modelfile found. Pulling base model '$ModelName'..."
        docker compose exec -T ollama sh -lc "ollama pull $ModelName"
    }
}

Test-CommandAvailable -Name "docker"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example. Update API keys in .env for data ingestion/training."
}

Write-Host "Pulling all images (frontend, backend, infra, local LLM)..."
docker compose -f docker-compose.yml pull

$gpuCompose = "docker-compose.gpu.yml"
$gpuEnabled = Test-Path $gpuCompose
$deployedWithGpu = $false

if ($gpuEnabled) {
    Write-Host "Trying GPU deployment for Ollama first..."
    docker compose -f docker-compose.yml -f $gpuCompose up -d --build
    if ($LASTEXITCODE -eq 0) {
        $deployedWithGpu = $true
    }
    else {
        Write-Host "GPU deployment failed. Falling back to CPU deployment."
        docker compose -f docker-compose.yml -f $gpuCompose down
    }
}

if (-not $deployedWithGpu) {
    Write-Host "Starting deployment with CPU-compatible Ollama..."
    docker compose -f docker-compose.yml up -d --build
    if ($LASTEXITCODE -ne 0) {
        throw "CPU deployment failed. Check docker compose logs."
    }
}

if (-not (Wait-ForHttp -Url "http://localhost:8000/api/v1/health/" -Attempts 90 -DelaySeconds 2)) {
    throw "Backend health check failed at http://localhost:8000/api/v1/health/"
}

if (-not (Wait-ForHttp -Url "http://localhost:8501" -Attempts 90 -DelaySeconds 2)) {
    throw "Frontend health check failed at http://localhost:8501"
}

Write-Host "Ensuring Mistral model is available in Ollama..."
Initialize-OllamaModel -ModelName $modelBootstrapName

if ($Train) {
    Write-Host "Triggering finance-news training pipeline..."
    $payload = @{
        symbols = ($TrainSymbols -split "," | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne "" })
        interval = $TrainInterval
        max_rows_per_symbol = $TrainRows
        sentiment_sample_size = $SentimentSamples
    } | ConvertTo-Json

    $resp = Invoke-RestMethod -Method Post -Uri "http://localhost:8000/api/v1/model/train-finance-news" -Body $payload -ContentType "application/json"
    $resp | ConvertTo-Json -Depth 8
}

if ($FineTune) {
    Write-Host "Running full QLoRA fine-tuning pipeline..."
    $fineTuneScript = Join-Path $PSScriptRoot "run_llm_finetune.ps1"
    if (-not (Test-Path $fineTuneScript)) {
        throw "Fine-tune script not found: $fineTuneScript"
    }

    & $fineTuneScript `
        -AdapterName $AdapterName `
        -OllamaModelName $OllamaFineTunedModelName `
        -TrainerMode $FineTuneTrainerMode `
        -DatasetLimit $FineTuneDatasetLimit `
        -Epochs $FineTuneEpochs `
        -LearningRate $FineTuneLearningRate `
        -BatchSize $FineTuneBatchSize `
        -GradAccum $FineTuneGradAccum
}

Write-Host "Deployment complete."
Write-Host "Backend:   http://localhost:8000"
Write-Host "Frontend:  http://localhost:8501"
Write-Host "Ollama:    http://localhost:11434"
Write-Host "Prometheus:http://localhost:9090"
Write-Host "Grafana:   http://localhost:3000"
Write-Host "GPU mode:  $deployedWithGpu"
Write-Host "RAG mode:  cpu-context + ollama-generation"
Write-Host "Fine-tune: $FineTune"
Write-Host "Trainer:   $FineTuneTrainerMode"
