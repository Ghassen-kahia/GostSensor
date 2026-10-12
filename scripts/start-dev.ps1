# Starts the backend API (+ engine) and the dashboard dev server in two windows.
# Prerequisites: python -m ghost.etl.build_all and python -m ghost.ml.train have been run once.
$root = Split-Path -Parent $PSScriptRoot
Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$root\backend'; python -m ghost.api.main"
Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$root\frontend'; npm run dev"
Write-Host "API:       http://localhost:8000/docs"
Write-Host "Dashboard: http://localhost:5173"
