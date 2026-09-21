[CmdletBinding()]
param(
    [ValidateRange(10, 180)]
    [int]$TimeoutSeconds = 90
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
$frontendPortLine = Get-Content -LiteralPath $EnvironmentFile | Where-Object { $_ -match '^FRONTEND_PORT=' } | Select-Object -First 1
$FrontendPort = if ($frontendPortLine) { ($frontendPortLine -split '=', 2)[1].Trim() } else { "8083" }
if ($FrontendPort -notmatch '^\d{2,5}$') {
    throw "FRONTEND_PORT must be a valid TCP port."
}
try {
    docker info | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Docker engine is unavailable." }
}
catch {
    throw "Docker Desktop is installed but its Linux engine is unavailable. Start Docker Desktop and retry."
}

Push-Location $RepositoryRoot
try {
    docker compose config --quiet
    if ($LASTEXITCODE -ne 0) { throw "Docker Compose configuration is invalid." }
    $migrationId = ([string](docker compose ps --all --quiet migrations)).Trim()
    if (-not $migrationId) {
        throw "Migration service is missing. Run .\scripts\docker-up.ps1 first."
    }
    $migrationExitCode = (docker inspect --format '{{.State.ExitCode}}' $migrationId).Trim()
    $migrationStatus = (docker inspect --format '{{.State.Status}}' $migrationId).Trim()
    if ($migrationStatus -ne "exited" -or $migrationExitCode -ne "0") {
        docker compose logs --tail 100 migrations
        throw "Database migrations did not complete successfully."
    }
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $services = @("postgres", "redis", "backend", "frontend")

    foreach ($service in $services) {
        do {
            $containerId = ([string](docker compose ps --quiet $service)).Trim()
            $status = if ($containerId) {
                (docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' $containerId).Trim()
            }
            else {
                "missing"
            }

            if ($status -eq "healthy") { break }
            if ((Get-Date) -ge $deadline) {
                docker compose ps
                throw "Service '$service' did not become healthy before the timeout (last state: $status)."
            }
            Start-Sleep -Seconds 2
        } while ($true)
        Write-Host "$service is healthy." -ForegroundColor Green
    }

    $frontend = Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$FrontendPort/" -TimeoutSec 10
    if ($frontend.StatusCode -ne 200) { throw "Frontend verification failed." }
    foreach ($endpoint in @("health", "health/live", "health/ready")) {
        $backend = Invoke-RestMethod "http://127.0.0.1:$FrontendPort/api/v1/$endpoint" -TimeoutSec 10
        if ($backend.status -ne "ok") { throw "Caddy API proxy verification failed: $endpoint" }
    }
    $spa = Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$FrontendPort/closure-spa-verification" -TimeoutSec 10
    if ($spa.StatusCode -ne 200 -or $spa.Content -ne $frontend.Content) {
        throw "SPA fallback verification failed."
    }
    Write-Host "Migrations exit 0, four healthchecks, health/live/ready via Caddy, and SPA fallback passed." -ForegroundColor Green
}
finally {
    Pop-Location
}
