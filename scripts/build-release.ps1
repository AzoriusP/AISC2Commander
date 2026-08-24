$ErrorActionPreference = 'Stop'

$ProjectRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$Version = '0.1.0-beta'
$PackageName = "AISC2Commander-v$Version-win-x64"
$DistRoot = [IO.Path]::GetFullPath((Join-Path $ProjectRoot 'dist'))
$PackageDir = [IO.Path]::GetFullPath((Join-Path $DistRoot $PackageName))
$BinDir = [IO.Path]::GetFullPath((Join-Path $ProjectRoot 'build\release-bin'))
$WorkDir = [IO.Path]::GetFullPath((Join-Path $ProjectRoot 'build\release-work'))
$SpecDir = [IO.Path]::GetFullPath((Join-Path $ProjectRoot 'build\release-spec'))
$ProjectPrefix = $ProjectRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar

foreach ($Target in @($PackageDir, $BinDir, $WorkDir, $SpecDir)) {
    if (-not $Target.StartsWith($ProjectPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to clean path outside project: $Target"
    }
    if (Test-Path -LiteralPath $Target) {
        Remove-Item -LiteralPath $Target -Recurse -Force
    }
    New-Item -ItemType Directory -Path $Target | Out-Null
}

$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$VoicePython = Join-Path $ProjectRoot '.voice-venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) {
    throw 'Missing .venv. Run .\scripts\bootstrap.ps1 first.'
}
if (-not (Test-Path -LiteralPath $VoicePython)) {
    throw 'Missing .voice-venv. Run .\scripts\setup-voice.ps1 first.'
}

Set-Location -LiteralPath $ProjectRoot
& $Python -m pip install -e '.[gui]'
& $VoicePython -m pip install 'pyinstaller==6.21.0'

& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name 'AISC2CommanderGUI' `
    --distpath $BinDir `
    --workpath (Join-Path $WorkDir 'gui') `
    --specpath $SpecDir `
    --paths 'src' `
    --collect-all 'sounddevice' `
    'scripts\gui_entry.py'

& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --console `
    --name 'AISC2CommanderBackend' `
    --distpath $BinDir `
    --workpath (Join-Path $WorkDir 'backend') `
    --specpath $SpecDir `
    --paths 'src' `
    --collect-all 'sounddevice' `
    'scripts\backend_entry.py'

& $VoicePython -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --console `
    --name 'AISC2Whisper' `
    --distpath $BinDir `
    --workpath (Join-Path $WorkDir 'whisper') `
    --specpath $SpecDir `
    --collect-all 'faster_whisper' `
    --collect-all 'ctranslate2' `
    --collect-all 'av' `
    --collect-all 'tokenizers' `
    --collect-all 'huggingface_hub' `
    --collect-all 'onnxruntime' `
    'scripts\transcribe_local.py'

Copy-Item -LiteralPath (Join-Path $BinDir 'AISC2CommanderGUI.exe') -Destination $PackageDir
Copy-Item -LiteralPath (Join-Path $BinDir 'AISC2CommanderBackend.exe') -Destination $PackageDir
Copy-Item -LiteralPath (Join-Path $BinDir 'AISC2Whisper.exe') -Destination $PackageDir

$ConfigDir = Join-Path $PackageDir 'config'
$AssetsDir = Join-Path $PackageDir 'assets\map_images'
$ModelsDir = Join-Path $PackageDir 'models\whisper'
New-Item -ItemType Directory -Path $ConfigDir, $AssetsDir, $ModelsDir | Out-Null
Copy-Item -LiteralPath 'config\command_plans.json' -Destination $ConfigDir
Copy-Item -LiteralPath 'config\llm.env.example' -Destination $ConfigDir
Copy-Item -LiteralPath 'config\openai.env.example' -Destination $ConfigDir
Copy-Item -LiteralPath 'config\voice.env.example' -Destination $ConfigDir
Copy-Item -LiteralPath 'config\voice.env.example' -Destination (Join-Path $ConfigDir 'voice.env')
Copy-Item -LiteralPath 'release\README.md' -Destination $PackageDir
Copy-Item -LiteralPath 'release\TESTING_TERMS.md' -Destination $PackageDir
Copy-Item -LiteralPath 'release\THIRD_PARTY_NOTICES.md' -Destination $PackageDir
Copy-Item -LiteralPath 'release\assets\main-window.png' -Destination (Join-Path $PackageDir 'assets')

$ZipPath = Join-Path $DistRoot "$PackageName.zip"
$ChecksumPath = "$ZipPath.sha256"
if (Test-Path -LiteralPath $ZipPath) {
    Remove-Item -LiteralPath $ZipPath -Force
}
if (Test-Path -LiteralPath $ChecksumPath) {
    Remove-Item -LiteralPath $ChecksumPath -Force
}
Compress-Archive -LiteralPath $PackageDir -DestinationPath $ZipPath -CompressionLevel Optimal
$Hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $ZipPath).Hash.ToLowerInvariant()
Set-Content -LiteralPath $ChecksumPath -Encoding ascii -NoNewline -Value "$Hash  $PackageName.zip"

Write-Host 'Release package:' (Resolve-Path -LiteralPath $ZipPath)
Write-Host 'SHA-256:' $Hash
