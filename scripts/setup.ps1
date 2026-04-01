Write-Host "Setting up BINFIN environment..."

if (!(Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example"
}

python -m pip install --upgrade pip
pip install -r requirements.txt
Write-Host "Setup complete."
