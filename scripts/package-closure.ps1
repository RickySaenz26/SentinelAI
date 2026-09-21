[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$RepositoryRoot = (Resolve-Path -LiteralPath (Split-Path -Parent $PSScriptRoot)).Path
if ((Split-Path -Leaf $RepositoryRoot) -ne 'SentinelAI-Sprint-1B.1-Closure-Candidate') {
    throw 'Packaging is restricted to the Closure-Candidate workspace.'
}
$ArchivePath = Join-Path (Split-Path -Parent $RepositoryRoot) 'SentinelAI-Sprint-1B.1-Closure-Candidate.zip'
if (Test-Path -LiteralPath $ArchivePath) {
    throw 'Archive already exists. Preserve or rename it explicitly before packaging again.'
}
$ExcludedDirectories = @('.git', '.venv', 'node_modules', 'dist', 'coverage', 'htmlcov',
    '.pytest_cache', '.ruff_cache', '__pycache__', 'logs', 'tmp', 'qa_report_render')
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
        if ($entry.FullName -match '(^|/)(\.env|node_modules|dist|__pycache__|logs|\.venv)(/|$)') {
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
