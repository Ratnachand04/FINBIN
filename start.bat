@echo off
setlocal

set SCRIPT_DIR=%~dp0
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%scripts\deploy_model.ps1" %*
if %errorlevel% neq 0 (
    echo Deployment failed.
    exit /b %errorlevel%
)
