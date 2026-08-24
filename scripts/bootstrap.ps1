$ErrorActionPreference = 'Stop'

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot

if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    python -m venv .venv
}

& '.\.venv\Scripts\python.exe' -m pip install --upgrade pip
& '.\.venv\Scripts\python.exe' -m pip install -e '.[test]'

if (-not (Test-Path -LiteralPath 'vendor\s2client-proto\.git')) {
    git clone --depth 1 https://github.com/Blizzard/s2client-proto.git vendor/s2client-proto
}

if (-not (Test-Path -LiteralPath 'vendor\s2client-api\.git')) {
    git clone --depth 1 --filter=blob:none --sparse https://github.com/Blizzard/s2client-api.git vendor/s2client-api
    git -C vendor/s2client-api sparse-checkout set maps docs include src/sc2api
}

Write-Host 'Bootstrap complete.'
Write-Host 'Run: .\scripts\run.ps1'

