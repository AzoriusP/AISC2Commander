$ErrorActionPreference = 'Stop'

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot

if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    throw 'Missing .venv. Run .\scripts\bootstrap.ps1 first.'
}

& '.\.venv\Scripts\python.exe' -m pip install -e '.[gui]'
& '.\.venv\Scripts\python.exe' -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name 'AISC2CommanderGUI' `
    --paths 'src' `
    --collect-all 'sounddevice' `
    --add-data 'assets\about;assets\about' `
    'scripts\gui_entry.py'

Copy-Item -LiteralPath 'dist\AISC2CommanderGUI.exe' -Destination 'AISC2CommanderGUI.exe' -Force
Remove-Item -LiteralPath 'dist\AISC2CommanderGUI.exe' -Force
Write-Host 'GUI build complete:' (Resolve-Path -LiteralPath 'AISC2CommanderGUI.exe')
