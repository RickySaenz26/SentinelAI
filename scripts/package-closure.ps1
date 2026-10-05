[CmdletBinding()]
param(
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$')]
    [string]$ArtifactBaseName = 'SentinelAI-Sprint-1B.1-Closure-Final-20260923'
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'package-file-policy.ps1')
if ($ArtifactBaseName.EndsWith('.zip', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'ArtifactBaseName must not include the .zip extension.'
}
$RepositoryRoot = (Resolve-Path -LiteralPath (Split-Path -Parent $PSScriptRoot)).Path
$CanonicalRepositoryRoot = [IO.Path]::GetFullPath('C:\SentinelAI\platform').TrimEnd('\')
$LegacyWorkspaceName = 'SentinelAI-Sprint-1B.1-Closure-Candidate'
$ResolvedRepositoryRoot = [IO.Path]::GetFullPath($RepositoryRoot).TrimEnd('\')
$IsCanonicalRepository = $ResolvedRepositoryRoot.Equals(
    $CanonicalRepositoryRoot, [StringComparison]::OrdinalIgnoreCase)
$IsLegacyClosureWorkspace = (Split-Path -Leaf $ResolvedRepositoryRoot) -eq $LegacyWorkspaceName
if (-not ($IsCanonicalRepository -or $IsLegacyClosureWorkspace)) {
    throw 'Packaging is restricted to the canonical repository or an explicit Closure-Candidate workspace.'
}

$GitRoot = (& git -C $RepositoryRoot rev-parse --show-toplevel 2>$null)
if ($LASTEXITCODE -ne 0 -or -not $GitRoot) {
    throw 'Packaging requires an authoritative Git worktree.'
}
$ResolvedGitRoot = [IO.Path]::GetFullPath(($GitRoot | Select-Object -First 1)).TrimEnd('\')
if (-not $ResolvedGitRoot.Equals($ResolvedRepositoryRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Packaging root does not match the authoritative Git worktree root.'
}
$ExpectedBranch = 'remediation/sprint-1b1-closure-gates'
$CurrentBranch = (& git -C $RepositoryRoot branch --show-current)
if ($LASTEXITCODE -ne 0 -or $CurrentBranch -ne $ExpectedBranch) {
    throw "Packaging requires the approved branch: $ExpectedBranch"
}

$RequiredRepositoryFiles = @(
    'backend\pyproject.toml',
    'backend\alembic.ini',
    'compose.yaml',
    'scripts\backend-quality.ps1'
)
foreach ($RequiredFile in $RequiredRepositoryFiles) {
    if (-not (Test-Path -LiteralPath (Join-Path $RepositoryRoot $RequiredFile) -PathType Leaf)) {
        throw "Repository authority check failed; missing required file: $RequiredFile"
    }
}

$RecoveryCheckpoint = '40fad40cf90664a8744ce2036a522cf02e9e5b27'
& git -C $RepositoryRoot merge-base --is-ancestor $RecoveryCheckpoint HEAD
if ($LASTEXITCODE -ne 0) {
    throw 'Current HEAD does not descend from the approved Sprint 1B.1 recovery checkpoint.'
}
$ArchivePath = Join-Path (Split-Path -Parent $RepositoryRoot) "$ArtifactBaseName.zip"
if (Test-Path -LiteralPath $ArchivePath) {
    throw 'Archive already exists. Preserve or rename it explicitly before packaging again.'
}
$Files = @(Get-ChildItem -LiteralPath $RepositoryRoot -Recurse -File -Force | Where-Object {
    $relative = [IO.Path]::GetRelativePath($RepositoryRoot, $_.FullName)
    if ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw 'Packaging refuses reparse-point files.'
    }
    Test-ClosurePackagePath $relative
})
Add-Type -AssemblyName System.IO.Compression
$archive = [IO.Compression.ZipFile]::Open($ArchivePath, [IO.Compression.ZipArchiveMode]::Create)
try {
    foreach ($file in ($Files | Sort-Object FullName)) {
        $relative = [IO.Path]::GetRelativePath($RepositoryRoot, $file.FullName).Replace('\', '/')
        $entry = "$ArtifactBaseName/" + $relative
        [IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
            $archive, $file.FullName, $entry, [IO.Compression.CompressionLevel]::Optimal) | Out-Null
    }
}
finally { $archive.Dispose() }
$inspection = [IO.Compression.ZipFile]::OpenRead($ArchivePath)
try {
    if ($inspection.Entries.Count -ne $Files.Count) { throw 'ZIP entry count mismatch.' }
    foreach ($entry in $inspection.Entries) {
        $relative = $entry.FullName.Substring($ArtifactBaseName.Length + 1)
        if (-not (Test-ClosurePackagePath $relative)) {
            throw "Excluded item found in ZIP: $($entry.FullName)"
        }
        # Read every entry fully; decompression failure fails the packaging gate.
        $stream = $entry.Open()
        try { $stream.CopyTo([IO.Stream]::Null) }
        finally { $stream.Dispose() }
    }
    Write-Host "ZIP inspected: $($inspection.Entries.Count) entries; excluded data absent."
}
finally { $inspection.Dispose() }
Get-FileHash -LiteralPath $ArchivePath -Algorithm SHA256
