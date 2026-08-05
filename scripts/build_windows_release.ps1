param(
    [ValidateSet("Basic", "Assisted")]
    [string]$Profile = "Basic",
    [switch]$SkipTauriBuild,
    [switch]$AllowDirtyDevelopmentBuild,
    [string]$SupersedesCandidate = ""
)

$ErrorActionPreference = "Stop"

if ($Profile -eq "Assisted") {
    $assistedArguments = @()
    if ($SkipTauriBuild) {
        $assistedArguments += "-SkipTauriBuild"
    }
    if ($AllowDirtyDevelopmentBuild) {
        $assistedArguments += "-AllowDirtyDevelopmentBuild"
    }
    if (-not [string]::IsNullOrWhiteSpace($SupersedesCandidate)) {
        $assistedArguments += @("-SupersedesCandidate", $SupersedesCandidate)
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
        [string]$WorkingDirectory,
        [string]$CargoTargetDirectory = ""
    )
    Write-Step $Label
    $previousCargoTargetDirectory = [Environment]::GetEnvironmentVariable("CARGO_TARGET_DIR", "Process")
    $useIsolatedCargoTarget = -not [string]::IsNullOrWhiteSpace($CargoTargetDirectory)
    Push-Location $WorkingDirectory
    try {
        if ($useIsolatedCargoTarget) {
            [Environment]::SetEnvironmentVariable("CARGO_TARGET_DIR", $CargoTargetDirectory, "Process")
        }
        & $FilePath @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "$Label failed with exit code $LASTEXITCODE"
        }
    }
    finally {
        if ($useIsolatedCargoTarget) {
            [Environment]::SetEnvironmentVariable("CARGO_TARGET_DIR", $previousCargoTargetDirectory, "Process")
        }
        Pop-Location
    }
}

function Assert-CargoTargetDirectory {
    param(
        [string]$ManifestPath,
        [string]$ExpectedTargetDirectory,
        [string]$WorkingDirectory
    )
    $expectedFull = [IO.Path]::GetFullPath($ExpectedTargetDirectory).TrimEnd('\')
    $previousCargoTargetDirectory = [Environment]::GetEnvironmentVariable("CARGO_TARGET_DIR", "Process")
    Push-Location $WorkingDirectory
    try {
        [Environment]::SetEnvironmentVariable("CARGO_TARGET_DIR", $expectedFull, "Process")
        $metadataText = (& cargo metadata --manifest-path $ManifestPath --format-version 1 --no-deps | Out-String)
        if ($LASTEXITCODE -ne 0) {
            throw "cargo metadata failed while validating the isolated release target."
        }
        $metadata = $metadataText | ConvertFrom-Json
        $actualFull = [IO.Path]::GetFullPath([string]$metadata.target_directory).TrimEnd('\')
        if ($actualFull -ne $expectedFull) {
            throw "Cargo target isolation mismatch. Expected $expectedFull, actual $actualFull"
        }
        Write-Host "Isolated Cargo target: $actualFull"
    }
    finally {
        [Environment]::SetEnvironmentVariable("CARGO_TARGET_DIR", $previousCargoTargetDirectory, "Process")
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
$cargoTargetDir = Join-Path $repoRoot ".release\cargo-target\basic"
$releaseDir = Join-Path $cargoTargetDir "release"
$stageRoot = Join-Path $repoRoot ".release\basic"
$stageResources = Join-Path $stageRoot "resources"

Write-Step "Check branch"
$branch = (& git -C $repoRoot branch --show-current).Trim()
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($branch)) {
    throw "Cannot resolve the current Git branch."
}
if ($branch -ne "main") {
    throw "Release build must run on main. Current branch: $branch"
}
Write-Host "Branch: $branch"

$sourceCommit = (& git -C $repoRoot rev-parse HEAD).Trim()
$shortCommit = (& git -C $repoRoot rev-parse --short=8 HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $sourceCommit -notmatch '^[0-9a-fA-F]{40}$' -or $shortCommit -notmatch '^[0-9a-fA-F]{7,12}$') {
    throw "Cannot resolve a valid Git source commit."
}
$sourceCommit = $sourceCommit.ToLowerInvariant()
$shortCommit = $shortCommit.ToLowerInvariant()

Write-Step "Check clean git status"
$status = @(& git -C $repoRoot status --short --untracked-files=all)
if ($LASTEXITCODE -ne 0) {
    throw "Cannot inspect the Git working tree."
}
$dirtyEntries = @($status | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) })
$worktreeDirty = $dirtyEntries.Count -gt 0
if ($worktreeDirty -and -not $AllowDirtyDevelopmentBuild) {
    $dirtyEntries | ForEach-Object { Write-Host $_ }
    throw "Working tree is not clean. Commit changes or explicitly use -AllowDirtyDevelopmentBuild for a non-publishable development candidate."
}
if ($worktreeDirty) {
    Write-Host "Dirty development override accepted; this candidate will remain non-publishable." -ForegroundColor Yellow
}
else {
    Write-Host "Working tree: clean"
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
$builtAt = (Get-Date).ToUniversalTime()
$buildStamp = $builtAt.ToString("yyyyMMddTHHmmssZ")
$dirtySuffix = if ($worktreeDirty) { "-dirty" } else { "" }
$candidateId = "$appVersion-$shortCommit-$buildStamp$dirtySuffix"
$supersedesCandidateValue = $SupersedesCandidate.Trim()
Write-Host "Candidate: $candidateId"

$safetyArguments = @(
    "scripts/check_release_build_safety.py",
    "--repo-root", $repoRoot
)
foreach ($cleanupRoot in @(
    $stageRoot,
    $cargoTargetDir,
    (Join-Path $repoRoot ".release\post-package-gate\$appVersion\$candidateId\basic"),
    (Join-Path $repoRoot ".release\artifacts\$appVersion\$candidateId\basic")
)) {
    $safetyArguments += @("--cleanup-root", $cleanupRoot)
}
Invoke-Checked "Validate release build cleanup safety" "python" $safetyArguments $repoRoot

Write-Step "Validate isolated Cargo target"
Assert-CargoTargetDirectory `
    (Join-Path $tauriDir "Cargo.toml") `
    $cargoTargetDir `
    $repoRoot

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
    throw "The Basic profile requires bundled AutoDock Vina."
}
if (-not $includesBundledPython) {
    throw "The Basic profile requires the bundled backend Python runtime."
}
if ($includesBundledRdkit -or $includesBundledMeeko -or (Test-Path -LiteralPath $sitePackagesPath)) {
    throw "The Basic profile must not contain RDKit, Meeko, or Lib/site-packages."
}
if (Test-Path -LiteralPath $scriptsPath) {
    throw "The Basic profile must not contain Python Scripts or Meeko preparation CLIs."
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
Invoke-Checked "cargo check" "cargo" @("check", "--manifest-path", "apps/desktop/src-tauri/Cargo.toml") $repoRoot $cargoTargetDir

if ($SkipTauriBuild) {
    Write-Step "Skip Tauri build"
    Write-Host "Tauri build and packaged Basic smoke test skipped by -SkipTauriBuild."
}
else {
    Write-Step "Clean stale Tauri release resources and bundles"
    foreach ($relativePath in @("backend", "frontend", "examples", "resources", "DockStart", "bundle", "nsis", "wix")) {
        Remove-ReleasePath $releaseDir (Join-Path $releaseDir $relativePath)
    }

    Invoke-Checked `
        "Build DockStart Basic desktop installers" `
        "npm.cmd" `
        @("run", "tauri", "--", "build", "--config", "src-tauri/tauri.basic.conf.json", "--bundles", "msi,nsis", "--ci") `
        $desktopDir `
        $cargoTargetDir

    Write-Step "Validate release artifacts"
    $bundleDir = Join-Path $releaseDir "bundle"
    $tauriMsi = Join-Path $bundleDir "msi\DockStart_${appVersion}_x64_en-US.msi"
    $tauriNsis = Join-Path $bundleDir "nsis\DockStart_${appVersion}_x64-setup.exe"
    $expectedMsi = Join-Path $bundleDir "msi\DockStart_${candidateId}_Basic_x64_en-US.msi"
    $expectedNsis = Join-Path $bundleDir "nsis\DockStart_${candidateId}_Basic_x64-setup.exe"
    foreach ($artifact in @($tauriMsi, $tauriNsis)) {
        if (-not (Test-Path -LiteralPath $artifact -PathType Leaf)) {
            throw "Expected Tauri release artifact is missing: $artifact"
        }
    }

    Write-Step "Extract MSI for the post-package Basic gate"
    $postPackageGateRoot = Join-Path $repoRoot ".release\post-package-gate"
    $postPackageExtract = Join-Path $postPackageGateRoot "$appVersion\$candidateId\basic"
    $postPackagePrefix = [IO.Path]::GetFullPath($postPackageGateRoot).TrimEnd('\') + '\'
    $postPackageExtractFull = [IO.Path]::GetFullPath($postPackageExtract)
    if (-not $postPackageExtractFull.StartsWith($postPackagePrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to extract the MSI outside .release/post-package-gate: $postPackageExtractFull"
    }
    if (Test-Path -LiteralPath $postPackageExtractFull) {
        Remove-Item -LiteralPath $postPackageExtractFull -Recurse -Force
    }
    New-Item -ItemType Directory -Path $postPackageExtractFull -Force | Out-Null
    $postPackageLog = Join-Path $postPackageGateRoot "$candidateId-basic-msiexec.log"
    $msiArguments = @(
        "/a",
        "`"$tauriMsi`"",
        "TARGETDIR=`"$postPackageExtractFull`"",
        "/qn",
        "/L*v",
        "`"$postPackageLog`""
    )
    $msiProcess = Start-Process -FilePath "msiexec.exe" -ArgumentList $msiArguments -Wait -PassThru -WindowStyle Hidden
    if ($msiProcess.ExitCode -ne 0) {
        throw "MSI administrative extraction failed with exit code $($msiProcess.ExitCode). Log: $postPackageLog"
    }
    $layoutManifests = @(
        Get-ChildItem -LiteralPath $postPackageExtractFull -Recurse -File -Filter "toolchain_manifest.json" |
            Where-Object { $_.Directory.Name -eq "resources" }
    )
    if ($layoutManifests.Count -ne 1) {
        throw "Expected exactly one extracted toolchain manifest, found $($layoutManifests.Count)."
    }
    $postPackageLayout = $layoutManifests[0].Directory.Parent.FullName

    Invoke-Checked `
        "Post-package Basic docking regression" `
        "python" `
        @("scripts/verify_basic_release.py", $postPackageLayout) `
        $repoRoot

    foreach ($rename in @(@($tauriMsi, $expectedMsi), @($tauriNsis, $expectedNsis))) {
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
    $profileArtifactDir = Join-Path $artifactArchiveRoot "$appVersion\$candidateId\basic"
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
    $artifactSha256 = [ordered]@{}
    foreach ($record in @($artifactRecords)) {
        $artifactName = [string]$record.name
        $artifactSha256[$artifactName] = [string]$record.sha256
    }
    $artifactManifest = [ordered]@{
        "app_version" = $appVersion
        "candidate" = $true
        "candidate_id" = $candidateId
        "source_commit" = $sourceCommit
        "source_branch" = $branch
        "built_at" = $builtAt.ToString("o")
        "profile" = "Basic"
        "release_profile" = "basic_stable"
        "maturity" = "local_candidate"
        "worktree_dirty" = $worktreeDirty
        "publishable" = $false
        "release_status" = "candidate"
        "supersedes_candidate" = $supersedesCandidateValue
        "gates" = [ordered]@{
            "branch" = [ordered]@{
                "status" = "passed"
                "required" = "main"
                "actual" = $branch
            }
            "worktree" = [ordered]@{
                "status" = if ($worktreeDirty) { "development_override" } else { "passed" }
                "dirty" = $worktreeDirty
                "allow_dirty_development_build" = [bool]$AllowDirtyDevelopmentBuild
            }
            "development" = "passed"
            "post_package" = "passed"
            "post_install" = "not_applicable_basic_candidate"
            "scientific_acceptance" = [ordered]@{
                "ad4_flexible_1fpu" = "not_run_by_builder"
                "ad4_multiple_ligands_5x72" = "not_run_by_builder"
                "ad4_serial_screening" = "not_run_by_builder"
            }
        }
        "artifact_sha256" = $artifactSha256
        "artifacts" = @($artifactRecords)
    }
    $artifactManifestPath = Join-Path $stageRoot "artifact-manifest.json"
    $archivedArtifactManifestPath = Join-Path $profileArtifactFull "artifact-manifest.json"
    $artifactManifestJson = $artifactManifest | ConvertTo-Json -Depth 8
    $artifactManifestJson | Set-Content -LiteralPath $artifactManifestPath -Encoding UTF8
    $artifactManifestJson | Set-Content -LiteralPath $archivedArtifactManifestPath -Encoding UTF8
    $artifactManifest | ConvertTo-Json -Depth 6
    Write-Host "Artifact manifest: $artifactManifestPath"
    Write-Host "Archived artifacts: $profileArtifactFull"
    Write-Host "Candidate only: publishable=false" -ForegroundColor Yellow
}

Write-Step "Done"
Write-Host "Profile: Basic candidate"
Write-Host "Stage:   $stageRoot"
Write-Host "Release: $releaseDir"
Write-Host "Do not commit target/, dist/, installers, .release/, or bundle outputs."
