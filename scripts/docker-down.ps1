[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$RepositoryRoot = Split-Path -Parent $PSScriptRoot

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker CLI was not found. Install Docker Desktop and start its Linux engine."
}
try {
    docker info | Out-Null
}
catch {
    throw "Docker Desktop is installed but its Linux engine is unavailable. Start Docker Desktop and retry."
}

Push-Location $RepositoryRoot
try {
    # Deliberately omit --volumes: local PostgreSQL and Redis data is retained.
    docker compose down
}
finally {
    Pop-Location
}
