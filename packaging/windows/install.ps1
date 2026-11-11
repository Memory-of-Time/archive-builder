$ErrorActionPreference = "Stop"
$Source = Join-Path $PSScriptRoot "ArchiveBuilder"
$CLISource = Join-Path $PSScriptRoot "ArchiveBuilderCLI"
$Destination = Join-Path $env:LOCALAPPDATA "Programs\ArchiveBuilder"
if (-not (Test-Path $Source)) { throw "ArchiveBuilder application folder was not found." }
if (-not (Test-Path $CLISource)) { throw "ArchiveBuilderCLI application folder was not found." }
if (Test-Path $Destination) { Remove-Item $Destination -Recurse -Force }
New-Item -ItemType Directory -Path $Destination -Force | Out-Null
Copy-Item "$Source\*" $Destination -Recurse -Force
$CLIDestination = Join-Path $Destination "ArchiveBuilderCLI"
Copy-Item $CLISource $CLIDestination -Recurse -Force
$CLIWrapper = Join-Path $Destination "archive-builder.cmd"
$CLIWrapperText = "@echo off`r`n`"%~dp0ArchiveBuilderCLI\ArchiveBuilderCLI.exe`" %*`r`n"
$CLIWrapperText | Set-Content $CLIWrapper -Encoding ascii
$Shell = New-Object -ComObject WScript.Shell
$DesktopShortcut = $Shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath("Desktop")) "Archive Builder.lnk"))
$DesktopShortcut.TargetPath = Join-Path $Destination "ArchiveBuilder.exe"
$DesktopShortcut.WorkingDirectory = $Destination
$DesktopShortcut.Save()
$StartDirectory = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Archive Builder"
New-Item -ItemType Directory -Path $StartDirectory -Force | Out-Null
$StartShortcut = $Shell.CreateShortcut((Join-Path $StartDirectory "Archive Builder.lnk"))
$StartShortcut.TargetPath = Join-Path $Destination "ArchiveBuilder.exe"
$StartShortcut.WorkingDirectory = $Destination
$StartShortcut.Save()
$UninstallShortcut = $Shell.CreateShortcut((Join-Path $StartDirectory "Uninstall Archive Builder.lnk"))
$UninstallShortcut.TargetPath = "powershell.exe"
$UninstallShortcut.Arguments = "-NoProfile -File `"$(Join-Path $Destination 'uninstall.ps1')`""
$UninstallShortcut.WorkingDirectory = $Destination
$UninstallShortcut.Save()
Copy-Item (Join-Path $PSScriptRoot "uninstall.ps1") $Destination -Force
Write-Host "Archive Builder was installed successfully."
