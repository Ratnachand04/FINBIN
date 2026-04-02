param(
    [string]$AdapterName = "binfin-mistral-qlora",
    [string]$OllamaModelName = "binfin-mistral-finance",
    [ValidateSet("gpu-qlora", "cpu-lora")]
    [string]$TrainerMode = "gpu-qlora",
    [int]$DatasetLimit = 15000,
    [double]$Epochs = 1.0,
    [double]$LearningRate = 0.0002,
    [int]$BatchSize = 1,
    [int]$GradAccum = 16
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "Starting core stack services needed for fine-tuning..."
docker compose up -d postgres ollama

Write-Host "Exporting SFT dataset from PostgreSQL..."
docker compose --profile llm-train run --rm llm-trainer python3 export_dataset.py --limit $DatasetLimit

if ($TrainerMode -eq "cpu-lora") {
    $trainScript = "train_lora_cpu.py"
}
else {
    $trainScript = "train_qlora.py"
}

Write-Host "Running $TrainerMode training for Mistral..."
docker compose --profile llm-train run --rm llm-trainer python3 $trainScript --adapter-name $AdapterName --epochs $Epochs --learning-rate $LearningRate --batch-size $BatchSize --grad-accum $GradAccum

$adapterDir = Join-Path $root "llm_trainer\artifacts\output\adapters\$AdapterName"
if (-not (Test-Path $adapterDir)) {
    throw "Adapter directory not found: $adapterDir"
}

Write-Host "Generating Ollama Modelfile..."
$tmpDir = "/tmp/binfin_$($TrainerMode -replace '-', '_')_$AdapterName"
Write-Host "Copying adapter into Ollama container..."
docker compose exec -T ollama sh -lc "mkdir -p $tmpDir"
docker compose cp "$adapterDir" "ollama:$tmpDir/adapter"

docker compose --profile llm-train run --rm llm-trainer python3 package_ollama.py --adapter-dir "$tmpDir/adapter" --model-name $OllamaModelName

$modelfilePath = Join-Path $root "llm_trainer\artifacts\ollama\Modelfile"
if (-not (Test-Path $modelfilePath)) {
    throw "Modelfile not found: $modelfilePath"
}

docker compose cp "$modelfilePath" "ollama:$tmpDir/Modelfile"

Write-Host "Building fine-tuned model in Ollama..."
docker compose exec -T ollama sh -lc "ollama pull mistral:7b-instruct-q4_K_M ; ollama create $OllamaModelName -f $tmpDir/Modelfile"

Write-Host "Fine-tuned model is ready in Ollama: $OllamaModelName (trainer: $TrainerMode)"
