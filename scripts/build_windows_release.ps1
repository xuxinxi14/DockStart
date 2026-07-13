param(
    [ValidateSet("Basic", "Assisted")]
    [string]$Profile = "Basic",
    [switch]$SkipTauriBuild
)

$ErrorActionPreference = "Stop"

if ($Profile -eq "Assisted") {
    $assistedArguments = @()
    if ($SkipTauriBuild) {
        $assistedArguments += "-SkipTauriBuild"
    }
    & (Join-Path $PSScriptRoot "build_windows_assisted_release.ps1") @assistedArguments
    exit $LASTEXITCODE
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

function Remove-ReleasePath {
    param(
        [string]$ReleaseRoot,
        [string]$TargetPath
    )
    $releaseFull = [IO.Path]::GetFullPath($ReleaseRoot).TrimEnd('\') + '\'
    $targetFull = [IO.Path]::GetFullPath($TargetPath)
    if (-not $targetFull.StartsWith($releaseFull, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to delete a path outside the Tauri release directory: $targetFull"
    }
    if (Test-Path -LiteralPath $targetFull) {
        Write-Host "Remove stale release path: $targetFull"
        Remove-Item -LiteralPath $targetFull -Recurse -Force
    }
}

function Assert-FileHash {
    param(
        [string]$Path,
        [string]$ExpectedSha256,
        [string]$Label
    )
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Label is missing: $Path"
    }
    $actual = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $ExpectedSha256.ToLowerInvariant()) {
        throw "$Label sha256 mismatch. Expected $ExpectedSha256, actual $actual"
    }
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$desktopDir = Join-Path $repoRoot "apps\desktop"
$tauriDir = Join-Path $desktopDir "src-tauri"
$releaseDir = Join-Path $tauriDir "target\release"
$stageRoot = Join-Path $repoRoot ".release\basic"
$stageResources = Join-Path $stageRoot "resources"

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
$appVersion = [string]$uniqueVersions[0]
Write-Host "Version: $appVersion"

Invoke-Checked `
    "Prepare deterministic Basic release stage" `
    "python" `
    @("scripts/prepare_basic_release_resources.py", "--repo-root", $repoRoot) `
    $repoRoot

Write-Step "Validate Basic release profile"
$stageManifestPath = Join-Path $stageResources "toolchain_manifest.json"
if (-not (Test-Path -LiteralPath $stageManifestPath -PathType Leaf)) {
    throw "Basic stage manifest is missing: $stageManifestPath"
}
$stageManifest = Get-Content -LiteralPath $stageManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ([string]$stageManifest.release_profile -ne "basic_stable") {
    throw "Basic stage manifest has an unexpected release_profile."
}

$stageVina = Join-Path $stageResources "vina\vina.exe"
$stagePython = Join-Path $stageResources "python\python.exe"
$includesBundledVina = Test-Path -LiteralPath $stageVina -PathType Leaf
$includesBundledPython = Test-Path -LiteralPath $stagePython -PathType Leaf
$sitePackagesPath = Join-Path $stageResources "python\Lib\site-packages"
$scriptsPath = Join-Path $stageResources "python\Scripts"
$includesBundledRdkit = Test-Path -LiteralPath (Join-Path $sitePackagesPath "rdkit")
$includesBundledMeeko = Test-Path -LiteralPath (Join-Path $sitePackagesPath "meeko")
$stageBytecode = @(
    Get-ChildItem -LiteralPath $stageRoot -Recurse -File |
        Where-Object { $_.Extension -in ".pyc", ".pyo" }
)
$stagePycache = @(
    Get-ChildItem -LiteralPath $stageRoot -Recurse -Directory -Filter "__pycache__"
)

if (-not $includesBundledVina) {
    throw "Basic Stable requires bundled AutoDock Vina."
}
if (-not $includesBundledPython) {
    throw "Basic Stable requires the bundled backend Python runtime."
}
if ($includesBundledRdkit -or $includesBundledMeeko -or (Test-Path -LiteralPath $sitePackagesPath)) {
    throw "Basic Stable must not contain RDKit, Meeko, or Lib/site-packages."
}
if (Test-Path -LiteralPath $scriptsPath) {
    throw "Basic Stable must not contain Python Scripts or Meeko preparation CLIs."
}
if ($stageBytecode.Count -gt 0 -or $stagePycache.Count -gt 0) {
    throw "Basic stage contains generated Python bytecode/cache files."
}
if ($stageManifest.includes_bundled_rdkit -ne $false -or $stageManifest.includes_bundled_meeko -ne $false) {
    throw "Basic manifest must explicitly report RDKit/Meeko as not bundled."
}

Assert-FileHash $stageVina ([string]$stageManifest.bundled_vina.sha256) "Bundled Vina"
Assert-FileHash $stagePython ([string]$stageManifest.bundled_python.sha256) "Bundled backend Python"

$requiredStageFiles = @(
    "backend\adapters\__init__.py",
    "backend\dockstart_core\project.py",
    "frontend\package.json",
    "resources\licenses\AutoDock-Vina_LICENSE.txt",
    "resources\licenses\DockStart-Apache-2.0.txt",
    "resources\licenses\3Dmol_LICENSE.txt",
    "resources\licenses\React_LICENSE.txt",
    "resources\licenses\React-DOM_LICENSE.txt",
    "resources\licenses\Phosphor-Icons_LICENSE.txt",
    "resources\licenses\Tauri_LICENSE_APACHE-2.0.txt",
    "resources\licenses\Tauri_LICENSE_MIT.txt",
    "resources\licenses\Tauri-plugin-dialog_LICENSE.spdx",
    "resources\licenses\Serde_LICENSE-MIT.txt",
    "resources\licenses\Python_LICENSE.txt",
    "resources\licenses\THIRD_PARTY_NOTICES.md",
    "resources\examples\basic_pdbqt\manifest.json",
    "resources\examples\basic_pdbqt\project.json",
    "resources\examples\basic_pdbqt\receptor.pdbqt",
    "resources\examples\basic_pdbqt\ligand.pdbqt",
    "resources\examples\assisted_raw\manifest.json",
    "resources\examples\viewer_result\manifest.json"
)
foreach ($relativePath in $requiredStageFiles) {
    $path = Join-Path $stageRoot $relativePath
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Basic release stage is missing: $relativePath"
    }
}

$artifactProfile = [ordered]@{
    "app_version" = $appVersion
    "build_type" = "basic_distributable"
    "release_profile" = "basic_stable"
    "includes_bundled_vina" = $true
    "includes_bundled_python" = $true
    "bundled_python_role" = "backend_runtime"
    "includes_bundled_rdkit" = $false
    "includes_bundled_meeko" = $false
    "includes_conda_env" = $false
    "includes_demo_projects" = $true
    "includes_examples" = $true
    "basic_mode_expected" = "Bundled Vina can run Basic Mode when the user provides receptor/ligand PDBQT."
    "assisted_mode_expected" = "Requires a user-configured Python environment with RDKit/Meeko."
    "known_requirements" = @(
        "No bundled RDKit/Meeko",
        "No PLIP/ProLIF",
        "No Open Babel/MGLTools",
        "No drug efficacy judgment",
        "No bundled conda environment"
    )
}
$artifactProfile.GetEnumerator() | ForEach-Object {
    if ($_.Value -is [array]) {
        Write-Host "$($_.Key): $($_.Value -join '; ')"
    }
    else {
        Write-Host "$($_.Key): $($_.Value)"
    }
}
$profilePath = Join-Path $stageRoot "artifact-profile.json"
$artifactProfile | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $profilePath -Encoding UTF8

Invoke-Checked "Python unittest" "python" @("-m", "unittest", "discover", "-s", "backend/tests") $repoRoot
Invoke-Checked "npm run build" "npm.cmd" @("run", "build") $desktopDir
Invoke-Checked "cargo check" "cargo" @("check", "--manifest-path", "apps/desktop/src-tauri/Cargo.toml") $repoRoot

if ($SkipTauriBuild) {
    Write-Step "Skip Tauri build"
    Write-Host "Tauri build and packaged Basic smoke test skipped by -SkipTauriBuild."
}
else {
    Write-Step "Clean stale Tauri release resources and bundles"
    foreach ($relativePath in @("backend", "frontend", "examples", "resources", "bundle", "nsis", "wix")) {
        Remove-ReleasePath $releaseDir (Join-Path $releaseDir $relativePath)
    }

    Invoke-Checked `
        "Build DockStart Basic desktop installers" `
        "npm.cmd" `
        @("run", "tauri", "--", "build", "--config", "src-tauri/tauri.basic.conf.json", "--bundles", "msi,nsis", "--ci") `
        $desktopDir

    Invoke-Checked `
        "Post-package Basic docking regression" `
        "python" `
        @("scripts/verify_basic_release.py", $releaseDir) `
        $repoRoot

    Write-Step "Validate release artifacts"
    $bundleDir = Join-Path $releaseDir "bundle"
    $tauriMsi = Join-Path $bundleDir "msi\DockStart_${appVersion}_x64_en-US.msi"
    $tauriNsis = Join-Path $bundleDir "nsis\DockStart_${appVersion}_x64-setup.exe"
    $expectedMsi = Join-Path $bundleDir "msi\DockStart_${appVersion}_Basic_x64_en-US.msi"
    $expectedNsis = Join-Path $bundleDir "nsis\DockStart_${appVersion}_Basic_x64-setup.exe"
    foreach ($rename in @(@($tauriMsi, $expectedMsi), @($tauriNsis, $expectedNsis))) {
        if (-not (Test-Path -LiteralPath $rename[0] -PathType Leaf)) {
            throw "Expected Tauri release artifact is missing: $($rename[0])"
        }
        Move-Item -LiteralPath $rename[0] -Destination $rename[1]
    }
    foreach ($artifact in @($expectedMsi, $expectedNsis)) {
        if (-not (Test-Path -LiteralPath $artifact -PathType Leaf)) {
            throw "Expected release artifact is missing: $artifact"
        }
    }
    $allInstallers = @(
        Get-ChildItem -LiteralPath $bundleDir -Recurse -File |
            Where-Object { $_.Extension -eq ".msi" -or ($_.Extension -eq ".exe" -and $_.Name -like "*-setup.exe") }
    )
    if ($allInstallers.Count -ne 2) {
        $allInstallers | Select-Object FullName | Format-Table -AutoSize
        throw "Release bundle contains unexpected or stale installer artifacts."
    }

    $artifactArchiveRoot = Join-Path $repoRoot ".release\artifacts"
    $profileArtifactDir = Join-Path $artifactArchiveRoot "$appVersion\basic"
    $archivePrefix = [IO.Path]::GetFullPath($artifactArchiveRoot).TrimEnd('\') + '\'
    $profileArtifactFull = [IO.Path]::GetFullPath($profileArtifactDir)
    if (-not $profileArtifactFull.StartsWith($archivePrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to archive Basic artifacts outside .release/artifacts: $profileArtifactFull"
    }
    if (Test-Path -LiteralPath $profileArtifactFull) {
        Remove-Item -LiteralPath $profileArtifactFull -Recurse -Force
    }
    New-Item -ItemType Directory -Path $profileArtifactFull -Force | Out-Null
    $archivedMsi = Join-Path $profileArtifactFull (Split-Path -Leaf $expectedMsi)
    $archivedNsis = Join-Path $profileArtifactFull (Split-Path -Leaf $expectedNsis)
    Copy-Item -LiteralPath $expectedMsi -Destination $archivedMsi
    Copy-Item -LiteralPath $expectedNsis -Destination $archivedNsis

    $artifactRecords = foreach ($artifact in @($archivedMsi, $archivedNsis)) {
        $item = Get-Item -LiteralPath $artifact
        $repoPrefix = $repoRoot.TrimEnd('\') + '\'
        if (-not $item.FullName.StartsWith($repoPrefix, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Release artifact is outside the repository: $($item.FullName)"
        }
        [ordered]@{
            "name" = $item.Name
            "path" = $item.FullName.Substring($repoPrefix.Length).Replace('\', '/')
            "size_bytes" = $item.Length
            "sha256" = (Get-FileHash -LiteralPath $item.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    }
    $artifactManifest = [ordered]@{
        "app_version" = $appVersion
        "release_profile" = "basic_stable"
        "generated_at" = (Get-Date).ToUniversalTime().ToString("o")
        "artifacts" = @($artifactRecords)
    }
    $artifactManifestPath = Join-Path $stageRoot "artifact-manifest.json"
    $artifactManifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $artifactManifestPath -Encoding UTF8
    $artifactManifest | ConvertTo-Json -Depth 6
    Write-Host "Artifact manifest: $artifactManifestPath"
    Write-Host "Archived artifacts: $profileArtifactFull"
}

Write-Step "Done"
Write-Host "Profile: Basic Stable"
Write-Host "Stage:   $stageRoot"
Write-Host "Release: $releaseDir"
Write-Host "Do not commit target/, dist/, installers, .release/, or bundle outputs."
