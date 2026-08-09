param(
    [switch]$SkipTauriBuild,
    [switch]$SkipPostInstallGate,
    [switch]$AllowDirtyDevelopmentBuild,
    [string]$SupersedesCandidate = ""
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
        $metadataText = (& cargo metadata --locked --manifest-path $ManifestPath --format-version 1 --no-deps | Out-String)
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

function Get-SourceStateFingerprint {
    param([string]$RepoRoot)

    $diffText = (& git -C $RepoRoot diff --binary --no-ext-diff HEAD | Out-String)
    if ($LASTEXITCODE -ne 0) {
        throw "Cannot capture the Git diff for release provenance."
    }
    $untracked = @(& git -C $RepoRoot ls-files --others --exclude-standard | Sort-Object)
    if ($LASTEXITCODE -ne 0) {
        throw "Cannot enumerate untracked files for release provenance."
    }
    $records = foreach ($relativePath in $untracked) {
        $fullPath = Join-Path $RepoRoot ([string]$relativePath)
        if (-not (Test-Path -LiteralPath $fullPath -PathType Leaf)) {
            throw "Untracked release source is not a regular file: $relativePath"
        }
        "$relativePath`t$((Get-FileHash -LiteralPath $fullPath -Algorithm SHA256).Hash.ToLowerInvariant())"
    }
    $payload = $diffText + "`n--UNTRACKED--`n" + ([string]::Join("`n", @($records)))
    $sha256 = [Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString(
            $sha256.ComputeHash([Text.Encoding]::UTF8.GetBytes($payload))
        )).Replace("-", "").ToLowerInvariant()
    }
    finally {
        $sha256.Dispose()
    }
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$desktopDir = Join-Path $repoRoot "apps\desktop"
$tauriDir = Join-Path $desktopDir "src-tauri"
$cargoTargetDir = Join-Path $repoRoot ".release\cargo-target\assisted"
$releaseDir = Join-Path $cargoTargetDir "release"
$stageRoot = Join-Path $repoRoot ".release\assisted"
$stageResources = Join-Path $stageRoot "resources"

Write-Step "Check branch and clean worktree"
$branch = (& git -C $repoRoot branch --show-current).Trim()
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($branch)) {
    throw "Cannot resolve the current Git branch."
}
if ($branch -ne "main") {
    throw "Release build must run on main. Current branch: $branch"
}
$sourceCommit = (& git -C $repoRoot rev-parse HEAD).Trim()
$shortCommit = (& git -C $repoRoot rev-parse --short=8 HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $sourceCommit -notmatch '^[0-9a-fA-F]{40}$' -or $shortCommit -notmatch '^[0-9a-fA-F]{7,12}$') {
    throw "Cannot resolve a valid Git source commit."
}
$sourceCommit = $sourceCommit.ToLowerInvariant()
$shortCommit = $shortCommit.ToLowerInvariant()
$status = @(& git -C $repoRoot status --short --untracked-files=all)
if ($LASTEXITCODE -ne 0) {
    throw "Cannot inspect the Git working tree."
}
$dirtyEntries = @($status | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) })
$worktreeDirty = $dirtyEntries.Count -gt 0
$initialStatusFingerprint = [string]::Join("`n", @($dirtyEntries))
$initialSourceStateSha256 = Get-SourceStateFingerprint $repoRoot
if ($worktreeDirty -and -not $AllowDirtyDevelopmentBuild) {
    $dirtyEntries | ForEach-Object { Write-Host $_ }
    throw "Working tree is not clean. Commit changes or explicitly use -AllowDirtyDevelopmentBuild for a non-publishable development candidate."
}
if ($worktreeDirty) {
    Write-Host "Dirty development override accepted; this candidate will remain non-publishable." -ForegroundColor Yellow
}
else {
    Write-Host "Branch: $branch; working tree: clean"
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
$lockfileSha256 = [ordered]@{
    "apps/desktop/package-lock.json" = (Get-FileHash -LiteralPath (Join-Path $desktopDir "package-lock.json") -Algorithm SHA256).Hash.ToLowerInvariant()
    "apps/desktop/src-tauri/Cargo.lock" = (Get-FileHash -LiteralPath (Join-Path $tauriDir "Cargo.lock") -Algorithm SHA256).Hash.ToLowerInvariant()
}
$toolVersions = [ordered]@{
    "python" = ((& python --version 2>&1) | Out-String).Trim()
    "node" = ((& node --version 2>&1) | Out-String).Trim()
    "npm" = ((& npm.cmd --version 2>&1) | Out-String).Trim()
    "rustc" = ((& rustc --version 2>&1) | Out-String).Trim()
    "cargo" = ((& cargo --version 2>&1) | Out-String).Trim()
}
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
    (Join-Path $repoRoot ".release\post-package-gate\$appVersion\$candidateId\assisted"),
    (Join-Path $repoRoot ".release\artifacts\$appVersion\$candidateId\assisted"),
    (Join-Path $repoRoot ".release\install-gate")
)) {
    $safetyArguments += @("--cleanup-root", $cleanupRoot)
}
if (-not $SkipPostInstallGate) {
    $safetyArguments += "--require-no-existing-install"
}
Invoke-Checked "Validate release build and install-gate safety" "python" $safetyArguments $repoRoot

Write-Step "Validate isolated Cargo target"
Assert-CargoTargetDirectory `
    (Join-Path $tauriDir "Cargo.toml") `
    $cargoTargetDir `
    $repoRoot

Invoke-Checked `
    "Unified development gate" `
    "powershell.exe" `
    @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", (Join-Path $repoRoot "scripts\check_all.ps1"),
        "-RepoRoot", $repoRoot,
        "-CargoTargetDirectory", $cargoTargetDir,
        "-RequireReleaseResources"
    ) `
    $repoRoot

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
    "maturity" = "local_candidate"
    "candidate" = $true
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

if ($SkipTauriBuild) {
    Write-Step "Skip Tauri build"
    Write-Host "Gate 1 passed. Tauri build and Gate 2 were explicitly skipped."
    exit 0
}

Write-Step "Clean stale Tauri release resources and bundles"
foreach ($relativePath in @("backend", "frontend", "examples", "resources", "DockStart", "bundle", "nsis", "wix")) {
    Remove-ReleasePath $releaseDir (Join-Path $releaseDir $relativePath)
}

# Release linking has a substantially higher memory peak than the source
# gate. Default to one Cargo worker so a 16 GiB packaging host remains
# deterministic; an operator may explicitly provide a different value.
$previousCargoBuildJobs = [Environment]::GetEnvironmentVariable("CARGO_BUILD_JOBS", "Process")
if ([string]::IsNullOrWhiteSpace($previousCargoBuildJobs)) {
    [Environment]::SetEnvironmentVariable("CARGO_BUILD_JOBS", "1", "Process")
}
try {
    Invoke-Checked `
        "Build DockStart Assisted desktop installers" `
        "npm.cmd" `
        @("run", "tauri", "--", "build", "--config", "src-tauri/tauri.assisted.conf.json", "--bundles", "msi,nsis", "--ci", "--", "--locked") `
        $desktopDir `
        $cargoTargetDir
}
finally {
    [Environment]::SetEnvironmentVariable("CARGO_BUILD_JOBS", $previousCargoBuildJobs, "Process")
}

Write-Step "Validate installer artifacts"
$bundleDir = Join-Path $releaseDir "bundle"
$tauriMsi = Join-Path $bundleDir "msi\DockStart_${appVersion}_x64_en-US.msi"
$tauriNsis = Join-Path $bundleDir "nsis\DockStart_${appVersion}_x64-setup.exe"
$expectedMsi = Join-Path $bundleDir "msi\DockStart_${candidateId}_Assisted_x64_en-US.msi"
$expectedNsis = Join-Path $bundleDir "nsis\DockStart_${candidateId}_Assisted_x64-setup.exe"
foreach ($artifact in @($tauriMsi, $tauriNsis)) {
    if (-not (Test-Path -LiteralPath $artifact -PathType Leaf)) {
        throw "Expected Tauri release artifact is missing: $artifact"
    }
}

# Tauri does not promise to leave an exploded resource tree beside the release
# binary. Verify the files that are actually inside the MSI instead of reading
# a possibly stale or registered installation directory.
Write-Step "Extract MSI for the post-package Assisted gate"
$postPackageGateRoot = Join-Path $repoRoot ".release\post-package-gate"
$postPackageExtract = Join-Path $postPackageGateRoot "$appVersion\$candidateId\assisted"
$postPackagePrefix = [IO.Path]::GetFullPath($postPackageGateRoot).TrimEnd('\') + '\'
$postPackageExtractFull = [IO.Path]::GetFullPath($postPackageExtract)
if (-not $postPackageExtractFull.StartsWith($postPackagePrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to extract the MSI outside .release/post-package-gate: $postPackageExtractFull"
}
if (Test-Path -LiteralPath $postPackageExtractFull) {
    Remove-Item -LiteralPath $postPackageExtractFull -Recurse -Force
}
New-Item -ItemType Directory -Path $postPackageExtractFull -Force | Out-Null
$postPackageLog = Join-Path $postPackageGateRoot "$candidateId-assisted-msiexec.log"
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
    "Mandatory post-package Assisted preparation and docking gate" `
    "python" `
    @("scripts/verify_assisted_release.py", $postPackageLayout, "--gate", "post-package") `
    $repoRoot

foreach ($rename in @(@($tauriMsi, $expectedMsi), @($tauriNsis, $expectedNsis))) {
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
$profileArtifactDir = Join-Path $artifactArchiveRoot "$appVersion\$candidateId\assisted"
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
$artifactSha256 = [ordered]@{}
foreach ($record in @($records)) {
    $artifactName = [string]$record.name
    $artifactSha256[$artifactName] = [string]$record.sha256
}
Write-Step "Recheck source state before recording provenance"
$finalBranch = (& git -C $repoRoot branch --show-current).Trim()
$finalSourceCommit = (& git -C $repoRoot rev-parse HEAD).Trim().ToLowerInvariant()
if ($LASTEXITCODE -ne 0 -or $finalBranch -cne $branch -or $finalSourceCommit -cne $sourceCommit) {
    throw "The Git branch or HEAD changed during the release build; refusing to publish stale provenance."
}
$finalStatus = @(& git -C $repoRoot status --short --untracked-files=all)
if ($LASTEXITCODE -ne 0) {
    throw "Cannot recheck the Git working tree before provenance publication."
}
$finalDirtyEntries = @($finalStatus | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) })
$finalStatusFingerprint = [string]::Join("`n", @($finalDirtyEntries))
if ($finalStatusFingerprint -cne $initialStatusFingerprint) {
    throw "The Git working tree changed during the release build; refusing to publish a stale manifest."
}
$finalSourceStateSha256 = Get-SourceStateFingerprint $repoRoot
if ($finalSourceStateSha256 -ne $initialSourceStateSha256) {
    throw "Source bytes changed during the release build; refusing to publish a stale manifest."
}
foreach ($lockEntry in $lockfileSha256.GetEnumerator()) {
    $currentHash = (Get-FileHash -LiteralPath (Join-Path $repoRoot $lockEntry.Key) -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($currentHash -ne [string]$lockEntry.Value) {
        throw "Lock file changed during the release build: $($lockEntry.Key)"
    }
}

$artifactManifest = [ordered]@{
    "app_version" = $appVersion
    "candidate" = $true
    "candidate_id" = $candidateId
    "source_commit" = $sourceCommit
    "source_branch" = $branch
    "source_state_sha256" = $initialSourceStateSha256
    "built_at" = $builtAt.ToString("o")
    "profile" = "Assisted"
    "release_profile" = "assisted_stable"
    "maturity" = "local_candidate"
    "worktree_dirty" = $worktreeDirty
    "supersedes_candidate" = $supersedesCandidateValue
    "tool_versions" = $toolVersions
    "lockfiles_sha256" = $lockfileSha256
    "development_gate" = "passed"
    "post_package_gate" = "passed"
    "post_install_gate" = "pending"
    "release_status" = "candidate_incomplete"
    "publishable" = $false
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
        "post_install" = "pending"
        "scientific_acceptance" = [ordered]@{
            "ad4_flexible_1fpu" = "not_run_by_builder"
            "ad4_multiple_ligands_5x72" = "not_run_by_builder"
            "ad4_serial_screening" = "not_run_by_builder"
        }
    }
    "artifact_sha256" = $artifactSha256
    "post_install_gate_result" = $null
    "artifacts" = @($records)
}
$artifactManifestPath = Join-Path $stageRoot "artifact-manifest.json"
$archivedArtifactManifestPath = Join-Path $profileArtifactFull "artifact-manifest.json"
$artifactManifestJson = $artifactManifest | ConvertTo-Json -Depth 10
$artifactManifestJson | Set-Content -LiteralPath $artifactManifestPath -Encoding UTF8
$artifactManifestJson | Set-Content -LiteralPath $archivedArtifactManifestPath -Encoding UTF8

if ($SkipPostInstallGate) {
    Write-Step "Skip real post-install gate (development only)"
    foreach ($artifact in @($archivedMsi, $archivedNsis)) {
        $artifactName = Split-Path -Leaf $artifact
        $currentHash = (Get-FileHash -LiteralPath $artifact -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($currentHash -ne [string]$artifactSha256[$artifactName]) {
            throw "Archived installer changed after provenance capture: $artifactName"
        }
    }
    $artifactManifest["post_install_gate"] = "pending"
    $artifactManifest["release_status"] = "candidate_incomplete"
    $artifactManifest["publishable"] = $false
    $artifactManifest["gates"]["post_install"] = "skipped_development_only"
    $artifactManifest["post_install_gate_result"] = [ordered]@{
        "skip_reason" = "SkipPostInstallGate was explicitly set. This artifact set is not releasable."
    }
    $artifactManifestJson = $artifactManifest | ConvertTo-Json -Depth 10
    $artifactManifestJson | Set-Content -LiteralPath $artifactManifestPath -Encoding UTF8
    $artifactManifestJson | Set-Content -LiteralPath $archivedArtifactManifestPath -Encoding UTF8
    $artifactManifest | ConvertTo-Json -Depth 8
    Write-Host "Installers were built for development, but the post-install gate is pending. Do not publish them." -ForegroundColor Yellow
    exit 0
}

$installGateResultPath = Join-Path $repoRoot ".release\install-gate\post-install-gate.json"
$artifactManifest["post_install_gate"] = "running"
$artifactManifest["gates"]["post_install"] = "running"
$artifactManifestJson = $artifactManifest | ConvertTo-Json -Depth 10
$artifactManifestJson | Set-Content -LiteralPath $artifactManifestPath -Encoding UTF8
$artifactManifestJson | Set-Content -LiteralPath $archivedArtifactManifestPath -Encoding UTF8
$installGateStartedAt = (Get-Date).ToUniversalTime()

try {
    Invoke-Checked `
        "Mandatory real NSIS install, post-install verification, and uninstall gate" `
        "python" `
        @(
            "scripts/verify_installed_assisted_release.py",
            "--repo-root", $repoRoot,
            "--installer", $archivedNsis
        ) `
        $repoRoot

    if (-not (Test-Path -LiteralPath $installGateResultPath -PathType Leaf)) {
        throw "The post-install gate did not write its result JSON: $installGateResultPath"
    }
    $installGateResult = Get-Content -LiteralPath $installGateResultPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ([string]$installGateResult.status -ne "passed") {
        throw "The post-install gate result is not passed."
    }
    $archivedNsisName = Split-Path -Leaf $archivedNsis
    $archivedNsisHash = [string]$artifactSha256[$archivedNsisName]
    if ([string]$installGateResult.installer_sha256 -ne $archivedNsisHash) {
        throw "The post-install gate result does not belong to the archived NSIS artifact recorded in this run."
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

    foreach ($artifact in @($archivedMsi, $archivedNsis)) {
        $artifactName = Split-Path -Leaf $artifact
        $currentHash = (Get-FileHash -LiteralPath $artifact -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($currentHash -ne [string]$artifactSha256[$artifactName]) {
            throw "Archived installer changed during the post-install gate: $artifactName"
        }
    }
    $finalBranch = (& git -C $repoRoot branch --show-current).Trim()
    $finalSourceCommit = (& git -C $repoRoot rev-parse HEAD).Trim().ToLowerInvariant()
    if ($LASTEXITCODE -ne 0 -or $finalBranch -cne $branch -or $finalSourceCommit -cne $sourceCommit) {
        throw "The Git branch or HEAD changed during the post-install gate."
    }
    $finalStatus = @(& git -C $repoRoot status --short --untracked-files=all)
    if ($LASTEXITCODE -ne 0) {
        throw "Cannot recheck the Git working tree after the post-install gate."
    }
    $finalDirtyEntries = @($finalStatus | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) })
    if ([string]::Join("`n", @($finalDirtyEntries)) -cne $initialStatusFingerprint) {
        throw "The Git working tree changed during the post-install gate."
    }
    if ((Get-SourceStateFingerprint $repoRoot) -ne $initialSourceStateSha256) {
        throw "Source bytes changed during the post-install gate."
    }
    foreach ($lockEntry in $lockfileSha256.GetEnumerator()) {
        $currentHash = (Get-FileHash -LiteralPath (Join-Path $repoRoot $lockEntry.Key) -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($currentHash -ne [string]$lockEntry.Value) {
            throw "Lock file changed during the post-install gate: $($lockEntry.Key)"
        }
    }

    $artifactManifest["post_install_gate"] = "passed"
    $artifactManifest["release_status"] = "candidate_gates_passed"
    $artifactManifest["publishable"] = $false
    $artifactManifest["gates"]["post_install"] = "passed"
    $artifactManifest["post_install_gate_result"] = $installGateResult
}
catch {
    $artifactManifest["post_install_gate"] = "failed"
    $artifactManifest["release_status"] = "candidate_failed"
    $artifactManifest["publishable"] = $false
    $artifactManifest["gates"]["post_install"] = "failed"
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
    $artifactManifestJson = $artifactManifest | ConvertTo-Json -Depth 10
    $artifactManifestJson | Set-Content -LiteralPath $artifactManifestPath -Encoding UTF8
    $artifactManifestJson | Set-Content -LiteralPath $archivedArtifactManifestPath -Encoding UTF8
    throw
}

$artifactManifestJson = $artifactManifest | ConvertTo-Json -Depth 10
$artifactManifestJson | Set-Content -LiteralPath $artifactManifestPath -Encoding UTF8
$artifactManifestJson | Set-Content -LiteralPath $archivedArtifactManifestPath -Encoding UTF8
$artifactManifestJson

Write-Step "Done"
Write-Host "Profile: Assisted candidate"
Write-Host "Stage:   $stageRoot"
Write-Host "Release: $releaseDir"
Write-Host "Artifacts: $profileArtifactFull"
Write-Host "Release gates: development=passed, post-package=passed, post-install=passed"
Write-Host "The NSIS gate installed only below .release/install-gate and verified a clean uninstall."
Write-Host "Candidate only: publishable=false" -ForegroundColor Yellow
