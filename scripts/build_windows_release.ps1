param(
    [switch]$SkipTauriBuild
)

$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "== $Message ==" -ForegroundColor Cyan
}

function Invoke-Checked {
    param(
        [string]$Label,
        [string]$FilePath,
        [string[]]$Arguments,
        [string]$WorkingDirectory
    )
    Write-Step $Label
    Push-Location $WorkingDirectory
    try {
        & $FilePath @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "$Label failed with exit code $LASTEXITCODE"
        }
    }
    finally {
        Pop-Location
    }
}

function Read-JsonVersion {
    param([string]$Path)
    $content = Get-Content -LiteralPath $Path -Raw -Encoding UTF8
    try {
        $json = $content | ConvertFrom-Json
        return [string]$json.version
    }
    catch {
        # Windows PowerShell 5 can fail on package-lock.json because it contains
        # an empty-string package key. Fall back to the top-level version field.
        $match = [regex]::Match($content, '"version"\s*:\s*"([^"]+)"')
        if (-not $match.Success) {
            throw
        }
        return $match.Groups[1].Value
    }
}

function Read-RegexVersion {
    param(
        [string]$Path,
        [string]$Pattern
    )
    $content = Get-Content -LiteralPath $Path -Raw -Encoding UTF8
    $match = [regex]::Match($content, $Pattern)
    if (-not $match.Success) {
        throw "Cannot read version from $Path"
    }
    return $match.Groups[1].Value
}

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$desktopDir = Join-Path $repoRoot "apps\desktop"
$tauriDir = Join-Path $desktopDir "src-tauri"

Write-Step "Check branch"
$branch = (& git -C $repoRoot branch --show-current).Trim()
if ($branch -ne "main") {
    throw "Release build must run on main. Current branch: $branch"
}

Write-Step "Check clean git status"
$status = (& git -C $repoRoot status --short)
if ($status) {
    Write-Host $status
    throw "Working tree is not clean. Commit or discard changes before release build."
}

Write-Step "Check version consistency"
$versions = [ordered]@{
    "backend" = Read-RegexVersion (Join-Path $repoRoot "backend\dockstart_core\__init__.py") "__version__\s*=\s*`"([^`"]+)`""
    "package.json" = Read-JsonVersion (Join-Path $desktopDir "package.json")
    "package-lock.json" = Read-JsonVersion (Join-Path $desktopDir "package-lock.json")
    "Cargo.toml" = Read-RegexVersion (Join-Path $tauriDir "Cargo.toml") "version\s*=\s*`"([^`"]+)`""
    "Cargo.lock" = Read-RegexVersion (Join-Path $tauriDir "Cargo.lock") "name\s*=\s*`"dockstart-desktop`"\s+version\s*=\s*`"([^`"]+)`""
    "tauri.conf.json" = Read-JsonVersion (Join-Path $tauriDir "tauri.conf.json")
    "navigation" = Read-RegexVersion (Join-Path $desktopDir "src\navigation\pages.ts") "appVersion\s*=\s*`"([^`"]+)`""
}
$uniqueVersions = @($versions.Values | Select-Object -Unique)
if (@($uniqueVersions).Count -ne 1) {
    $versions.GetEnumerator() | ForEach-Object { Write-Host "$($_.Key): $($_.Value)" }
    throw "Version numbers are not consistent."
}
Write-Host "Version: $($uniqueVersions[0])"

Write-Step "Release artifact capability profile"
$resourcesDir = Join-Path $repoRoot "resources"
$profile = [ordered]@{
    "app_version" = $uniqueVersions[0]
    "build_type" = "lightweight_or_toolchain_assisted"
    "includes_bundled_vina" = (Test-Path -LiteralPath (Join-Path $resourcesDir "vina\vina.exe"))
    "includes_bundled_python" = (Test-Path -LiteralPath (Join-Path $resourcesDir "python\python.exe"))
    "includes_conda_env" = $false
    "includes_demo_projects" = (Test-Path -LiteralPath (Join-Path $repoRoot "examples\demo_basic_project")) -and (Test-Path -LiteralPath (Join-Path $repoRoot "examples\demo_assisted_project"))
    "includes_examples" = (Test-Path -LiteralPath (Join-Path $repoRoot "examples"))
    "basic_mode_expected" = "Requires AutoDock Vina plus user-provided receptor/ligand PDBQT."
    "assisted_mode_expected" = "Requires AutoDock Vina and a configured Python environment with RDKit/Meeko."
    "known_requirements" = @("No PLIP/ProLIF", "No Open Babel/MGLTools", "No drug efficacy judgment", "No bundled conda env in lightweight release")
}
$profile.GetEnumerator() | ForEach-Object {
    if ($_.Value -is [array]) {
        Write-Host "$($_.Key): $($_.Value -join '; ')"
    }
    else {
        Write-Host "$($_.Key): $($_.Value)"
    }
}

Invoke-Checked "Python unittest" "python" @("-m", "unittest", "discover", "-s", "backend/tests") $repoRoot
Invoke-Checked "npm run build" "npm.cmd" @("run", "build") $desktopDir
Invoke-Checked "cargo check" "cargo" @("check", "--manifest-path", "apps/desktop/src-tauri/Cargo.toml") $repoRoot

if ($SkipTauriBuild) {
    Write-Step "Skip Tauri build"
    Write-Host "Tauri build skipped by -SkipTauriBuild."
}
else {
    Invoke-Checked "npm run tauri build" "npm.cmd" @("run", "tauri", "build") $desktopDir
}

Write-Step "Release artifacts"
$releaseDir = Join-Path $tauriDir "target\release"
$bundleDir = Join-Path $releaseDir "bundle"
Write-Host "Release directory: $releaseDir"
Write-Host "Bundle directory:  $bundleDir"
if (Test-Path -LiteralPath $bundleDir) {
    Get-ChildItem -LiteralPath $bundleDir -Recurse -File |
        Where-Object { $_.Extension -in ".msi", ".exe" } |
        Select-Object FullName, Length, LastWriteTime |
        Format-Table -AutoSize
}
else {
    Write-Host "Bundle directory does not exist yet."
}

Write-Step "Done"
Write-Host "Do not commit target/, dist/, installers, or bundle outputs."
