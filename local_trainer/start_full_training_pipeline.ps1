Write-Host "1. Starting QLoRA targeted training (Base weights frozen, adapters training on RTX 4060)..."
python e:\BINFIN\local_trainer\train_crypto_qlora.py

Write-Host "2. Merging adapters into Base Mistral and Compiling into GGUF format..."
bash.exe e:\BINFIN\local_trainer\merge_and_compile.sh

Write-Host "3. Committing compiled model via Git LFS and pushing to tracking upstream..."
git add e:\BINFIN\docker\ollama\binfin-mistral.gguf
git commit -m "[Auto] Pack custom QLoRA Mistral model with crypto weights"
git push
