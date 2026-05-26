param(
    [switch]$SkipZip
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$distRoot = Join-Path $repoRoot "dist"
$buildRoot = Join-Path $repoRoot "build"
$releaseRoot = Join-Path $distRoot "release"

$pyprojectPath = Join-Path $repoRoot "pyproject.toml"
$pyprojectContent = Get-Content $pyprojectPath -Raw
$versionMatch = [regex]::Match($pyprojectContent, '(?m)^version\s*=\s*"([^"]+)"')
if (-not $versionMatch.Success) {
    throw "Unable to read version from pyproject.toml"
}

$version = $versionMatch.Groups[1].Value
$releaseTag = "v$version"
$appName = "TriggerAutoInput"
$artifactPrefix = "TriggerAutoInput-$releaseTag-win64"
$stageRoot = Join-Path $releaseRoot $appName
$appStage = $stageRoot
$appSpec = Join-Path $repoRoot "$appName.spec"
$exampleConfigSource = Join-Path $repoRoot "config\\example.json"
$exampleConfigStageDir = Join-Path $appStage "config"

Write-Host "Building release artifacts for $releaseTag"

if (Test-Path $buildRoot) {
    Remove-Item -LiteralPath $buildRoot -Recurse -Force
}

if (Test-Path $stageRoot) {
    Remove-Item -LiteralPath $stageRoot -Recurse -Force
}

New-Item -ItemType Directory -Path $appStage -Force | Out-Null
New-Item -ItemType Directory -Path $exampleConfigStageDir -Force | Out-Null

Push-Location $repoRoot
try {
    uv run pyinstaller `
        --noconfirm `
        --clean `
        --onedir `
        --uac-admin `
        --name $appName `
        --windowed `
        --hidden-import _sha2 `
        --exclude-module hashlib `
        --exclude-module _hashlib `
        mainWindow.py
}
finally {
    Pop-Location
}

if (Test-Path $appSpec) {
    Remove-Item -LiteralPath $appSpec -Force
}

$appDist = Join-Path $distRoot $appName
if (-not (Test-Path $appDist)) {
    throw "App build output not found: $appDist"
}

Copy-Item -Path (Join-Path $appDist "*") -Destination $appStage -Recurse -Force
Copy-Item -LiteralPath $exampleConfigSource -Destination $exampleConfigStageDir -Force
Copy-Item -LiteralPath (Join-Path $repoRoot "README.md") -Destination $appStage -Force

if (-not $SkipZip) {
    $zipPath = Join-Path $releaseRoot "$artifactPrefix.zip"
    if (Test-Path $zipPath) {
        [System.IO.File]::Delete($zipPath)
    }
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zipRetries = 3
    for ($attempt = 1; $attempt -le $zipRetries; $attempt++) {
        try {
            $zipArchive = [System.IO.Compression.ZipFile]::Open($zipPath, [System.IO.Compression.ZipArchiveMode]::Create)
            try {
                $stageFiles = Get-ChildItem -LiteralPath $appStage -Recurse -File
                foreach ($stageFile in $stageFiles) {
                    $entryName = $stageFile.FullName.Substring($releaseRoot.Length).TrimStart('\', '/')
                    [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
                        $zipArchive,
                        $stageFile.FullName,
                        $entryName,
                        [System.IO.Compression.CompressionLevel]::Optimal
                    ) | Out-Null
                }
            }
            finally {
                $zipArchive.Dispose()
            }
            break
        }
        catch {
            if (Test-Path $zipPath) {
                [System.IO.File]::Delete($zipPath)
            }
            if ($attempt -eq $zipRetries) {
                throw
            }
            Start-Sleep -Seconds 2
        }
    }
    Write-Host "Created zip archive: $zipPath"
}

Write-Host "Release staging directory: $stageRoot"
