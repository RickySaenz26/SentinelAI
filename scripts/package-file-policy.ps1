# Pure inventory predicate: importing this file never packages or writes anything.
function Test-ClosurePackagePath {
    param([Parameter(Mandatory)][string]$RelativePath)
    $parts = $RelativePath -split '[\\/]'
    $ExcludedDirectories = @('.git', '.venv', 'venv', 'node_modules', 'dist', 'build', '.next',
        'out', 'coverage', 'htmlcov', '.pytest_cache', '.ruff_cache', '.mypy_cache', '.cache',
        '__pycache__', 'logs', 'tmp', 'qa_report_render',
        'evidence-data', 'evidence-storage', 'evidence-keys')
    $ExcludedExtensions = @('.pyc', '.pyo', '.log', '.sqlite', '.sqlite3', '.db', '.pem', '.key',
        '.kek', '.evidence')
    $name = $parts[-1]
    if ([IO.Path]::IsPathRooted($RelativePath) -or $parts -contains '..' -or
        @($parts | Where-Object { $_ -in $ExcludedDirectories }).Count -gt 0) { return $false }
    return [IO.Path]::GetExtension($name) -notin $ExcludedExtensions -and
        $name -notlike '.coverage*' -and $name -notlike 'coverage*.json' -and
        $name -ne '.evidence.lock' -and $name -notlike 'stage-*.tmp' -and
        $name -ne '.evidence.coordinator.lock' -and
        ($name -notlike '.env*' -or $name -eq '.env.example') -and
        $name -notmatch '(?i)(credentials|recovery[-_]?codes|private[-_]?key)'
}
