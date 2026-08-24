$ErrorActionPreference = 'Stop'

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot

if (-not (Test-Path -LiteralPath '.voice-venv\Scripts\python.exe')) {
    if (Test-Path -LiteralPath '.venv\Scripts\python.exe') {
        & '.\.venv\Scripts\python.exe' -m venv .voice-venv
    } else {
        python -m venv .voice-venv
    }
}

& '.\.voice-venv\Scripts\python.exe' -m pip install --upgrade pip
& '.\.voice-venv\Scripts\python.exe' -m pip install 'faster-whisper==1.2.1'
Write-Host 'Local Whisper environment is ready.'
