$ErrorActionPreference = "Stop"
$Destination = Join-Path $env:LOCALAPPDATA "Programs\ArchiveBuilder"
$DesktopShortcut = Join-Path ([Environment]::GetFolderPath("Desktop")) "Archive Builder.lnk"
$StartDirectory = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Archive Builder"
Remove-Item $DesktopShortcut -Force -ErrorAction SilentlyContinue
Remove-Item $StartDirectory -Recurse -Force -ErrorAction SilentlyContinue
if (Test-Path $Destination) {
  Start-Process powershell.exe -ArgumentList "-NoProfile -WindowStyle Hidden -Command Start-Sleep 2; Remove-Item -LiteralPath '$Destination' -Recurse -Force" -WindowStyle Hidden
}
Write-Host "Archive Builder uninstall scheduled. Project folders were not removed."
