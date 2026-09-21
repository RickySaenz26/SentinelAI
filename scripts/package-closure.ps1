[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
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
$ArchivePath = Join-Path (Split-Path -Parent $RepositoryRoot) 'SentinelAI-Sprint-1B.1-Closure-Candidate.zip'
if (Test-Path -LiteralPath $ArchivePath) {
    throw 'Archive already exists. Preserve or rename it explicitly before packaging again.'
}
$ExcludedDirectories = @('.git', '.venv', 'venv', 'node_modules', 'dist', 'build', '.next',
    'out', 'coverage', 'htmlcov', '.pytest_cache', '.ruff_cache', '.mypy_cache', '.cache',
    '__pycache__', 'logs', 'tmp', 'qa_report_render')
$ExcludedExtensions = @('.pyc', '.pyo', '.log', '.sqlite', '.sqlite3', '.db', '.pem', '.key')
$Files = @(Get-ChildItem -LiteralPath $RepositoryRoot -Recurse -File -Force | Where-Object {
    $relative = [IO.Path]::GetRelativePath($RepositoryRoot, $_.FullName)
    $parts = $relative -split '[\\/]'
    $blockedPart = @($parts | Where-Object { $_ -in $ExcludedDirectories }).Count -gt 0
    -not $blockedPart -and $_.Extension -notin $ExcludedExtensions -and
        $_.Name -notlike '.coverage*' -and $_.Name -notlike 'coverage*.json' -and
        ($_.Name -notlike '.env*' -or $_.Name -eq '.env.example') -and
        $_.Name -notmatch '(?i)(credentials|recovery[-_]?codes|private[-_]?key)'
})
Add-Type -AssemblyName System.IO.Compression
$archive = [IO.Compression.ZipFile]::Open($ArchivePath, [IO.Compression.ZipArchiveMode]::Create)
try {
    foreach ($file in ($Files | Sort-Object FullName)) {
        $relative = [IO.Path]::GetRelativePath($RepositoryRoot, $file.FullName).Replace('\', '/')
        $entry = 'SentinelAI-Sprint-1B.1-Closure-Candidate/' + $relative
        [IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
            $archive, $file.FullName, $entry, [IO.Compression.CompressionLevel]::Optimal) | Out-Null
    }
}
finally { $archive.Dispose() }
$inspection = [IO.Compression.ZipFile]::OpenRead($ArchivePath)
try {
    if ($inspection.Entries.Count -ne $Files.Count) { throw 'ZIP entry count mismatch.' }
    foreach ($entry in $inspection.Entries) {
        if ($entry.FullName -match '(^|/)(\.env|node_modules|dist|build|\.next|out|coverage|htmlcov|__pycache__|logs|\.venv|venv)(/|$)') {
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
