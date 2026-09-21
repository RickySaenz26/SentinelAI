[CmdletBinding()]
param(
    [switch]$NoCache
)

$ErrorActionPreference = "Stop"
$RepositoryRoot = Split-Path -Parent $PSScriptRoot
$ComposeFile = Join-Path $RepositoryRoot "compose.yaml"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker CLI was not found. Install Docker Desktop and start its Linux engine."
}
if (-not (Test-Path -LiteralPath $ComposeFile)) {
    throw "compose.yaml was not found at $RepositoryRoot."
}
try {
    docker info | Out-Null
}
catch {
    throw "Docker Desktop is installed but its Linux engine is unavailable. Start Docker Desktop and retry."
}

Push-Location $RepositoryRoot
try {
    $Arguments = @("compose", "build")
    if ($NoCache) { $Arguments += "--no-cache" }
    & docker @Arguments
}
finally {
    Pop-Location
}
