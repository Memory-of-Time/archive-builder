$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
python scripts/verify_release.py
if ($LASTEXITCODE -ne 0) { throw "Release metadata verification failed" }
Remove-Item build,dist,release -Recurse -Force -ErrorAction SilentlyContinue
python -m PyInstaller `
  --noconfirm `
  --clean `
  --windowed `
  --onedir `
  --noupx `
  --name ArchiveBuilder `
  --icon assets/archivebuilder.ico `
  --version-file packaging/windows/version_info.txt `
  --add-data "assets/archivebuilder.png;assets" `
  --collect-all httpx `
  --collect-all httpcore `
  --collect-all bs4 `
  --collect-all dateutil `
  --collect-all openpyxl `
  run_app.py
if ($LASTEXITCODE -ne 0) { throw "Windows GUI build failed" }
python -m PyInstaller `
  --noconfirm `
  --clean `
  --console `
  --onedir `
  --noupx `
  --name ArchiveBuilderCLI `
  --version-file packaging/windows/version_info.txt `
  --collect-all httpx `
  --collect-all httpcore `
  --collect-all bs4 `
  --collect-all dateutil `
  --collect-all openpyxl `
  run_cli.py
if ($LASTEXITCODE -ne 0) { throw "Windows CLI build failed" }
if (-not (Test-Path "dist\ArchiveBuilder\ArchiveBuilder.exe")) { throw "ArchiveBuilder.exe was not built" }
if (-not (Test-Path "dist\ArchiveBuilderCLI\ArchiveBuilderCLI.exe")) { throw "ArchiveBuilderCLI.exe was not built" }
New-Item -ItemType Directory -Path release -Force | Out-Null
$Package = Join-Path $PWD "release\ArchiveBuilder-Windows-x64"
New-Item -ItemType Directory -Path $Package -Force | Out-Null
Copy-Item dist\ArchiveBuilder $Package\ArchiveBuilder -Recurse
Copy-Item dist\ArchiveBuilderCLI $Package\ArchiveBuilderCLI -Recurse
Copy-Item packaging\windows\install.ps1 $Package
Copy-Item 'packaging\windows\Install Archive Builder.cmd' $Package
Copy-Item packaging\windows\uninstall.ps1 $Package
Copy-Item 'packaging\windows\Uninstall Archive Builder.cmd' $Package
Copy-Item packaging\windows\README-WINDOWS.txt $Package
Copy-Item README.md $Package
$Zip = Join-Path $PWD "release\ArchiveBuilder-Windows-x64.zip"
Compress-Archive -Path "$Package\*" -DestinationPath $Zip -CompressionLevel Optimal
$Hash = (Get-FileHash $Zip -Algorithm SHA256).Hash.ToLower()
"$Hash  ArchiveBuilder-Windows-x64.zip" | Set-Content "release\ArchiveBuilder-Windows-x64.zip.sha256" -Encoding ascii
