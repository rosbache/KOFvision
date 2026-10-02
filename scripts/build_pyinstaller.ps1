param(
    [ValidateSet("onedir", "onefile")]
    [string]$Mode = "onedir",

    [string]$PythonExe = "python",

    [switch]$SkipDependencyInstall,

    [switch]$NoZip
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "..")
Set-Location $RepoRoot

$AppName = "KOFvision"
$EntryScript = "kof_viewer.py"

Write-Host "Repo root: $RepoRoot"
Write-Host "Build mode: $Mode"

if (-not (Test-Path $EntryScript)) {
    throw "Could not find $EntryScript in repo root."
}

if (-not $SkipDependencyInstall) {
    Write-Host "Installing build dependencies..."
    & $PythonExe -m pip install --upgrade pip
    & $PythonExe -m pip install -r requirements-build.txt
}

# Clean previous artifacts so stale files are not included in release output.
if (Test-Path "build") {
    Remove-Item -Recurse -Force "build"
}
if (Test-Path "dist") {
    Remove-Item -Recurse -Force "dist"
}

$PyInstallerArgs = @(
    "--noconfirm",
    "--clean",
    "--windowed",
    "--name", $AppName,
    "--collect-data", "matplotlib",
    "--collect-submodules", "matplotlib.backends",
    "--hidden-import", "PIL._tkinter_finder",
    $EntryScript
)

if ($Mode -eq "onefile") {
    $PyInstallerArgs = @("--onefile") + $PyInstallerArgs
} else {
    $PyInstallerArgs = @("--onedir") + $PyInstallerArgs
}

Write-Host "Running PyInstaller..."
& $PythonExe -m PyInstaller @PyInstallerArgs

$OutRoot = Join-Path $RepoRoot "dist"
$ReleaseRoot = Join-Path $OutRoot "$AppName-release"
if (Test-Path $ReleaseRoot) {
    Remove-Item -Recurse -Force $ReleaseRoot
}
New-Item -ItemType Directory -Path $ReleaseRoot | Out-Null

$ReadmeText = @"
KOFvision end-user package
==========================

How to run:
1. Open this folder.
2. Run KOFvision.exe.

Notes:
- This executable is built for Windows.
- Keep all files together if this is an onedir build.
- For best startup reliability, launch from a local disk (not directly from a network share).
"@
Set-Content -Path (Join-Path $ReleaseRoot "README-ENDUSER.txt") -Value $ReadmeText -Encoding UTF8

if ($Mode -eq "onefile") {
    Copy-Item -Path (Join-Path $OutRoot "$AppName.exe") -Destination (Join-Path $ReleaseRoot "$AppName.exe")
} else {
    Copy-Item -Recurse -Path (Join-Path $OutRoot $AppName) -Destination (Join-Path $ReleaseRoot $AppName)
}

$PackagePath = Join-Path $OutRoot "$AppName-release-$Mode.zip"
if (Test-Path $PackagePath) {
    Remove-Item -Force $PackagePath
}

if (-not $NoZip) {
    Compress-Archive -Path (Join-Path $ReleaseRoot "*") -DestinationPath $PackagePath
    Write-Host "Package ready: $PackagePath"
}

Write-Host "Build finished. Release folder: $ReleaseRoot"
