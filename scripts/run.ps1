$ErrorActionPreference = 'Stop'

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot

if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    throw 'Missing .venv. Run .\scripts\bootstrap.ps1 first.'
}

& '.\.venv\Scripts\python.exe' -m aisc2commander run @args

