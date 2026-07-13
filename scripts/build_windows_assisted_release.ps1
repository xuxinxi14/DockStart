param(
    [switch]$SkipTauriBuild,
    [switch]$SkipPostInstallGate
)

$ErrorActionPreference = "Stop"

if ($SkipTauriBuild -and $SkipPostInstallGate) {
    throw "SkipTauriBuild and SkipPostInstallGate cannot be combined. SkipTauriBuild produces no installer artifact."
}

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
        return [string](($content | ConvertFrom-Json).version)
    }
    catch {
        $match = [regex]::Match($content, '"version"\s*:\s*"([^"]+)"')
        if (-not $match.Success) { throw }
        return $match.Groups[1].Value
    }
}

function Read-RegexVersion {
    param([string]$Path, [string]$Pattern)
    $content = Get-Content -LiteralPath $Path -Raw -Encoding UTF8
    $match = [regex]::Match($content, $Pattern)
    if (-not $match.Success) { throw "Cannot read version from $Path" }
    return $match.Groups[1].Value
}

function Remove-ReleasePath {
    param([string]$ReleaseRoot, [string]$TargetPath)
    $releaseFull = [IO.Path]::GetFullPath($ReleaseRoot).TrimEnd('\') + '\'
    $targetFull = [IO.Path]::GetFullPath($TargetPath)
    if (-not $targetFull.StartsWith($releaseFull, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to delete a path outside the Tauri release directory: $targetFull"
    }
    if (Test-Path -LiteralPath $targetFull) {
        Remove-Item -LiteralPath $targetFull -Recurse -Force
    }
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$desktopDir = Join-Path $repoRoot "apps\desktop"
$tauriDir = Join-Path $desktopDir "src-tauri"
$releaseDir = Join-Path $tauriDir "target\release"
$stageRoot = Join-Path $repoRoot ".release\assisted"
$stageResources = Join-Path $stageRoot "resources"

Write-Step "Check branch and clean worktree"
$branch = (& git -C $repoRoot branch --show-current).Trim()
if ($branch -ne "main") { throw "Release build must run on main. Current branch: $branch" }
$status = (& git -C $repoRoot status --short)
if ($status) {
    Write-Host $status
    throw "Working tree is not clean. Commit or discard changes before release build."
}

Write-Step "Check version consistency"
$versions = [ordered]@{
    "backend" = Read-RegexVersion (Join-Path $repoRoot "backend\dockstart_core\__init__.py") '__version__\s*=\s*"([^"]+)"'
    "package.json" = Read-JsonVersion (Join-Path $desktopDir "package.json")
    "package-lock.json" = Read-JsonVersion (Join-Path $desktopDir "package-lock.json")
    "Cargo.toml" = Read-RegexVersion (Join-Path $tauriDir "Cargo.toml") 'version\s*=\s*"([^"]+)"'
    "Cargo.lock" = Read-RegexVersion (Join-Path $tauriDir "Cargo.lock") 'name\s*=\s*"dockstart-desktop"\s+version\s*=\s*"([^"]+)"'
    "tauri.conf.json" = Read-JsonVersion (Join-Path $tauriDir "tauri.conf.json")
    "navigation" = Read-RegexVersion (Join-Path $desktopDir "src\navigation\pages.ts") 'appVersion\s*=\s*"([^"]+)"'
}
$uniqueVersions = @($versions.Values | Select-Object -Unique)
if ($uniqueVersions.Count -ne 1) {
    $versions.GetEnumerator() | ForEach-Object { Write-Host "$($_.Key): $($_.Value)" }
    throw "Version numbers are not consistent."
}
$appVersion = [string]$uniqueVersions[0]
Write-Host "Version: $appVersion"

Invoke-Checked `
    "Prepare deterministic offline Assisted stage" `
    "python" `
    @("scripts/prepare_assisted_release_resources.py", "--repo-root", $repoRoot) `
    $repoRoot

Write-Step "Validate Assisted stage profile"
$manifestPath = Join-Path $stageResources "toolchain_manifest.json"
$manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ([string]$manifest.release_profile -ne "assisted_stable") { throw "Unexpected Assisted release profile." }
if ($manifest.includes_bundled_rdkit -ne $true -or $manifest.includes_bundled_meeko -ne $true) {
    throw "Assisted stage must include pinned RDKit and Meeko."
}
if ([string]$manifest.integrity_policy -notlike "*replacement*") {
    throw "Assisted manifest must preserve the user replacement policy."
}
foreach ($relativePath in @(
    "python\python.exe",
    "vina\vina.exe",
    "licenses\Meeko-LGPL-2.1.txt",
    "licenses\Gemmi-MPL-2.0.txt",
    "licenses\DockStart-Apache-2.0.txt",
    "licenses\3Dmol_LICENSE.txt",
    "licenses\React_LICENSE.txt",
    "licenses\React-DOM_LICENSE.txt",
    "licenses\Phosphor-Icons_LICENSE.txt",
    "licenses\Tauri_LICENSE_APACHE-2.0.txt",
    "licenses\Tauri_LICENSE_MIT.txt",
    "licenses\Tauri-plugin-dialog_LICENSE.spdx",
    "licenses\Serde_LICENSE-MIT.txt",
    "licenses\THIRD_PARTY_NOTICES.md",
    "sources\SOURCE_MANIFEST.json",
    "sources\meeko-0.7.1.tar.gz",
    "sources\gemmi-0.7.5.tar.gz",
    "sources\tqdm-4.67.1.tar.gz"
)) {
    $path = Join-Path $stageResources $relativePath
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Assisted stage is missing: $relativePath" }
}

$bytecode = @(Get-ChildItem -LiteralPath $stageRoot -Recurse -File | Where-Object { $_.Extension -in ".pyc", ".pyo" })
$pycache = @(Get-ChildItem -LiteralPath $stageRoot -Recurse -Directory -Filter "__pycache__")
if ($bytecode.Count -gt 0 -or $pycache.Count -gt 0) { throw "Assisted stage contains Python bytecode/cache files." }

$artifactProfile = [ordered]@{
    "app_version" = $appVersion
    "build_type" = "assisted_distributable"
    "release_profile" = "assisted_stable"
    "includes_bundled_vina" = $true
    "includes_bundled_python" = $true
    "includes_bundled_rdkit" = $true
    "includes_bundled_meeko" = $true
    "offline_preparation_expected" = $true
    "preparation_python_priority" = @("configured", "bundled", "current_environment")
    "scientific_boundary" = "PDB/SDF preparation still requires human review and does not prove binding or efficacy."
}
$artifactProfile | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $stageRoot "artifact-profile.json") -Encoding UTF8

# Gate 1 is intentionally before packaging. It exercises the exact staged tree
# from a Chinese path with network proxies disabled.
Invoke-Checked `
    "Mandatory development-layout Assisted preparation and docking gate" `
    "python" `
    @("scripts/verify_assisted_release.py", $stageRoot, "--gate", "development") `
    $repoRoot

Invoke-Checked "Python unittest" "python" @("-m", "unittest", "discover", "-s", "backend/tests") $repoRoot
Invoke-Checked "npm run build" "npm.cmd" @("run", "build") $desktopDir
Invoke-Checked "cargo check" "cargo" @("check", "--manifest-path", "apps/desktop/src-tauri/Cargo.toml") $repoRoot
Invoke-Checked "cargo test" "cargo" @("test", "--manifest-path", "apps/desktop/src-tauri/Cargo.toml") $repoRoot

if ($SkipTauriBuild) {
    Write-Step "Skip Tauri build"
    Write-Host "Gate 1 passed. Tauri build and Gate 2 were explicitly skipped."
    exit 0
}

Write-Step "Clean stale Tauri release resources and bundles"
foreach ($relativePath in @("backend", "frontend", "examples", "resources", "bundle", "nsis", "wix")) {
    Remove-ReleasePath $releaseDir (Join-Path $releaseDir $relativePath)
}

Invoke-Checked `
    "Build DockStart Assisted desktop installers" `
    "npm.cmd" `
    @("run", "tauri", "--", "build", "--config", "src-tauri/tauri.assisted.conf.json", "--bundles", "msi,nsis", "--ci") `
    $desktopDir

# Gate 2 uses the resource layout emitted by Tauri. Gate 3 below performs a
# real silent NSIS installation into .release/install-gate and verifies that
# actual installed layout before silently uninstalling it again.
Invoke-Checked `
    "Mandatory post-package Assisted preparation and docking gate" `
    "python" `
    @("scripts/verify_assisted_release.py", $releaseDir, "--gate", "post-package") `
    $repoRoot

Write-Step "Validate installer artifacts"
$bundleDir = Join-Path $releaseDir "bundle"
$tauriMsi = Join-Path $bundleDir "msi\DockStart_${appVersion}_x64_en-US.msi"
$tauriNsis = Join-Path $bundleDir "nsis\DockStart_${appVersion}_x64-setup.exe"
$expectedMsi = Join-Path $bundleDir "msi\DockStart_${appVersion}_Assisted_x64_en-US.msi"
$expectedNsis = Join-Path $bundleDir "nsis\DockStart_${appVersion}_Assisted_x64-setup.exe"
foreach ($rename in @(@($tauriMsi, $expectedMsi), @($tauriNsis, $expectedNsis))) {
    if (-not (Test-Path -LiteralPath $rename[0] -PathType Leaf)) {
        throw "Expected Tauri release artifact is missing: $($rename[0])"
    }
    Move-Item -LiteralPath $rename[0] -Destination $rename[1]
}
foreach ($artifact in @($expectedMsi, $expectedNsis)) {
    if (-not (Test-Path -LiteralPath $artifact -PathType Leaf)) { throw "Expected release artifact is missing: $artifact" }
}
$allInstallers = @(
    Get-ChildItem -LiteralPath $bundleDir -Recurse -File |
        Where-Object { $_.Extension -eq ".msi" -or ($_.Extension -eq ".exe" -and $_.Name -like "*-setup.exe") }
)
if ($allInstallers.Count -ne 2) {
    $allInstallers | Select-Object FullName | Format-Table -AutoSize
    throw "Assisted release bundle contains unexpected or stale installer artifacts."
}
$artifactArchiveRoot = Join-Path $repoRoot ".release\artifacts"
$profileArtifactDir = Join-Path $artifactArchiveRoot "$appVersion\assisted"
$archivePrefix = [IO.Path]::GetFullPath($artifactArchiveRoot).TrimEnd('\') + '\'
$profileArtifactFull = [IO.Path]::GetFullPath($profileArtifactDir)
if (-not $profileArtifactFull.StartsWith($archivePrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to archive Assisted artifacts outside .release/artifacts: $profileArtifactFull"
}
if (Test-Path -LiteralPath $profileArtifactFull) {
    Remove-Item -LiteralPath $profileArtifactFull -Recurse -Force
}
New-Item -ItemType Directory -Path $profileArtifactFull -Force | Out-Null
$archivedMsi = Join-Path $profileArtifactFull (Split-Path -Leaf $expectedMsi)
$archivedNsis = Join-Path $profileArtifactFull (Split-Path -Leaf $expectedNsis)
Copy-Item -LiteralPath $expectedMsi -Destination $archivedMsi
Copy-Item -LiteralPath $expectedNsis -Destination $archivedNsis

$records = foreach ($artifact in @($archivedMsi, $archivedNsis)) {
    $item = Get-Item -LiteralPath $artifact
    [ordered]@{
        "name" = $item.Name
        "path" = $item.FullName.Substring($repoRoot.TrimEnd('\').Length + 1).Replace('\', '/')
        "size_bytes" = $item.Length
        "sha256" = (Get-FileHash -LiteralPath $item.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    }
}
$artifactManifest = [ordered]@{
    "app_version" = $appVersion
    "release_profile" = "assisted_stable"
    "generated_at" = (Get-Date).ToUniversalTime().ToString("o")
    "development_gate" = "passed"
    "post_package_gate" = "passed"
    "post_install_gate" = "pending"
    "release_status" = "incomplete"
    "publishable" = $false
    "post_install_gate_result" = $null
    "artifacts" = @($records)
}
$artifactManifestPath = Join-Path $stageRoot "artifact-manifest.json"
$artifactManifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $artifactManifestPath -Encoding UTF8

if ($SkipPostInstallGate) {
    Write-Step "Skip real post-install gate (development only)"
    $artifactManifest["post_install_gate"] = "pending"
    $artifactManifest["release_status"] = "development_only"
    $artifactManifest["publishable"] = $false
    $artifactManifest["post_install_gate_result"] = [ordered]@{
        "skip_reason" = "SkipPostInstallGate was explicitly set. This artifact set is not releasable."
    }
    $artifactManifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $artifactManifestPath -Encoding UTF8
    $artifactManifest | ConvertTo-Json -Depth 8
    Write-Host "Installers were built for development, but the post-install gate is pending. Do not publish them." -ForegroundColor Yellow
    exit 0
}

$installGateResultPath = Join-Path $repoRoot ".release\install-gate\post-install-gate.json"
$artifactManifest["post_install_gate"] = "running"
$artifactManifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $artifactManifestPath -Encoding UTF8
$installGateStartedAt = (Get-Date).ToUniversalTime()

try {
    Invoke-Checked `
        "Mandatory real NSIS install, post-install verification, and uninstall gate" `
        "python" `
        @(
            "scripts/verify_installed_assisted_release.py",
            "--repo-root", $repoRoot,
            "--installer", $expectedNsis
        ) `
        $repoRoot

    if (-not (Test-Path -LiteralPath $installGateResultPath -PathType Leaf)) {
        throw "The post-install gate did not write its result JSON: $installGateResultPath"
    }
    $installGateResult = Get-Content -LiteralPath $installGateResultPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ([string]$installGateResult.status -ne "passed") {
        throw "The post-install gate result is not passed."
    }
    $expectedNsisHash = (Get-FileHash -LiteralPath $expectedNsis -Algorithm SHA256).Hash.ToLowerInvariant()
    if ([string]$installGateResult.installer_sha256 -ne $expectedNsisHash) {
        throw "The post-install gate result does not belong to the NSIS artifact built in this run."
    }
    if ([string]$installGateResult.install_root -ne ".release/install-gate/installed") {
        throw "The post-install gate did not use the required isolated install directory."
    }
    if ($installGateResult.verification.ok -ne $true -or [string]$installGateResult.verification.gate -ne "post-install") {
        throw "The actual installed layout was not verified with --gate post-install."
    }
    if ($installGateResult.uninstall.clean -ne $true -or $installGateResult.uninstall.install_directory_removed -ne $true) {
        throw "The post-install gate did not prove a clean silent uninstall."
    }

    $artifactManifest["post_install_gate"] = "passed"
    $artifactManifest["release_status"] = "passed"
    $artifactManifest["publishable"] = $true
    $artifactManifest["post_install_gate_result"] = $installGateResult
}
catch {
    $artifactManifest["post_install_gate"] = "failed"
    $artifactManifest["release_status"] = "failed"
    $artifactManifest["publishable"] = $false
    $installGateResultItem = Get-Item -LiteralPath $installGateResultPath -ErrorAction SilentlyContinue
    if ($null -ne $installGateResultItem -and $installGateResultItem.LastWriteTimeUtc -ge $installGateStartedAt) {
        $artifactManifest["post_install_gate_result"] = Get-Content -LiteralPath $installGateResultPath -Raw -Encoding UTF8 | ConvertFrom-Json
    }
    else {
        $artifactManifest["post_install_gate_result"] = [ordered]@{
            "status" = "failed"
            "error" = $_.Exception.Message
            "diagnostics" = ".release/install-gate/diagnostics"
        }
    }
    $artifactManifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $artifactManifestPath -Encoding UTF8
    throw
}

$artifactManifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $artifactManifestPath -Encoding UTF8
$artifactManifest | ConvertTo-Json -Depth 8

Write-Step "Done"
Write-Host "Profile: Assisted Stable"
Write-Host "Stage:   $stageRoot"
Write-Host "Release: $releaseDir"
Write-Host "Artifacts: $profileArtifactFull"
Write-Host "Release gates: development=passed, post-package=passed, post-install=passed"
Write-Host "The NSIS gate installed only below .release/install-gate and verified a clean uninstall."
