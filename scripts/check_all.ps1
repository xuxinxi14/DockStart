param(
    [string]$RepoRoot = "",
    [string]$CargoTargetDirectory = "",
    [switch]$RequireReleaseResources,
    [switch]$SkipNpmCi
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

if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}
else {
    $RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
}

$desktopDir = Join-Path $RepoRoot "apps\desktop"
$cargoManifest = Join-Path $desktopDir "src-tauri\Cargo.toml"
if (-not (Test-Path -LiteralPath $cargoManifest -PathType Leaf)) {
    throw "DockStart Cargo manifest is missing: $cargoManifest"
}

$previousCargoTarget = [Environment]::GetEnvironmentVariable("CARGO_TARGET_DIR", "Process")
$previousReleaseRequirement = [Environment]::GetEnvironmentVariable(
    "DOCKSTART_REQUIRE_RELEASE_RESOURCES",
    "Process"
)

try {
    if (-not [string]::IsNullOrWhiteSpace($CargoTargetDirectory)) {
        $cargoTargetFull = [IO.Path]::GetFullPath($CargoTargetDirectory)
        [Environment]::SetEnvironmentVariable("CARGO_TARGET_DIR", $cargoTargetFull, "Process")
        Write-Host "Cargo target directory: $cargoTargetFull"
    }

    [Environment]::SetEnvironmentVariable(
        "DOCKSTART_REQUIRE_RELEASE_RESOURCES",
        $(if ($RequireReleaseResources) { "1" } else { $null }),
        "Process"
    )

    Invoke-Checked "Git whitespace check" "git" @("diff", "--check") $RepoRoot
    Invoke-Checked "Python compile check" "python" @("-m", "compileall", "-q", "backend", "scripts") $RepoRoot
    Invoke-Checked "Python unittest" "python" @("-m", "unittest", "discover", "-s", "backend/tests") $RepoRoot

    if (-not $SkipNpmCi) {
        Invoke-Checked "npm clean install" "npm.cmd" @("ci", "--ignore-scripts") $desktopDir
    }
    Invoke-Checked "npm vulnerability audit" "npm.cmd" @(
        "audit",
        "--audit-level=high",
        "--registry=https://registry.npmjs.org"
    ) $desktopDir
    Invoke-Checked "Frontend async tests" "npm.cmd" @("run", "test:frontend:async") $desktopDir
    Invoke-Checked "TypeScript and Vite production build" "npm.cmd" @("run", "build") $desktopDir

    Invoke-Checked "Rust formatting" "cargo" @("fmt", "--manifest-path", $cargoManifest, "--", "--check") $RepoRoot
    Invoke-Checked "Rust check" "cargo" @("check", "--locked", "--manifest-path", $cargoManifest) $RepoRoot
    Invoke-Checked "Rust tests" "cargo" @("test", "--locked", "--manifest-path", $cargoManifest) $RepoRoot
    Invoke-Checked "Rust Clippy" "cargo" @(
        "clippy",
        "--locked",
        "--all-targets",
        "--manifest-path",
        $cargoManifest,
        "--",
        "-D",
        "warnings"
    ) $RepoRoot
}
finally {
    [Environment]::SetEnvironmentVariable("CARGO_TARGET_DIR", $previousCargoTarget, "Process")
    [Environment]::SetEnvironmentVariable(
        "DOCKSTART_REQUIRE_RELEASE_RESOURCES",
        $previousReleaseRequirement,
        "Process"
    )
}

Write-Step "All automated checks passed"
