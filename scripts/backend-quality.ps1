[CmdletBinding()]
param([switch]$NoBuild)
$ErrorActionPreference = 'Stop'
$RepositoryRoot = Split-Path -Parent $PSScriptRoot
$Project = 'sentinelai-closure-quality-' + [Guid]::NewGuid().ToString('N').Substring(0, 10)
$GateExit = 1
Push-Location -LiteralPath $RepositoryRoot
try {
    if (-not $NoBuild) {
        docker compose -p $Project -f compose.quality.yaml build quality
        if ($LASTEXITCODE -ne 0) { throw 'Quality image build failed.' }
    }
    docker compose -p $Project -f compose.quality.yaml up -d --wait postgres
    if ($LASTEXITCODE -ne 0) { throw 'Ephemeral PostgreSQL failed.' }
    docker compose -p $Project -f compose.quality.yaml run --rm -T quality
    $GateExit = $LASTEXITCODE
    docker compose -p $Project -f compose.quality.yaml run --rm -T --no-deps -v "${RepositoryRoot}:/repository:ro" quality python /repository/scripts/secret_scan.py /repository
    if ($LASTEXITCODE -ne 0) { $GateExit = 1 }
}
finally {
    # This randomly named project has only tmpfs storage, never user data volumes.
    docker compose -p $Project -f compose.quality.yaml down --remove-orphans
    if ($LASTEXITCODE -ne 0) { $GateExit = 1 }
    Pop-Location
}
exit $GateExit
