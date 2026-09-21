[CmdletBinding()]
param(
    [switch]$Build
)

$ErrorActionPreference = "Stop"
$RepositoryRoot = Split-Path -Parent $PSScriptRoot
$EnvironmentFile = Join-Path $RepositoryRoot ".env"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker CLI was not found. Install Docker Desktop and start its Linux engine."
}
if (-not (Test-Path -LiteralPath $EnvironmentFile)) {
    throw "Missing .env. Copy .env.example to .env, review the local-only values, and retry."
}
try {
    docker info | Out-Null
}
catch {
    throw "Docker Desktop is installed but its Linux engine is unavailable. Start Docker Desktop and retry."
}

Push-Location $RepositoryRoot
try {
    $Arguments = @("compose", "up", "--detach")
    if ($Build) { $Arguments += "--build" }
    & docker @Arguments
    Write-Host "SentinelAI is starting. Run .\scripts\docker-verify.ps1 to wait for healthchecks." -ForegroundColor Green
}
finally {
    Pop-Location
}
