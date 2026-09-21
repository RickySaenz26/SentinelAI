#requires -Version 7.2
[CmdletBinding()]
param(
    [ValidateRange(60, 300)]
    [int]$TimeoutSeconds = 180
)

$ErrorActionPreference = 'Stop'
$RepositoryRoot = (Resolve-Path -LiteralPath (Split-Path -Parent $PSScriptRoot)).Path
$BaseComposePath = Join-Path $RepositoryRoot 'compose.yaml'
$SmokeComposePath = Join-Path $RepositoryRoot 'compose.smoke.yaml'
$HelperPath = Join-Path $PSScriptRoot 'authenticated-compose-smoke-helper.py'
$CookieName = '__Host-sentinel_session'
$CsrfHeaderName = 'X-CSRF-Token'

function Get-RandomHex {
    param([ValidateRange(8, 64)][int]$ByteCount = 24)

    $bytes = [byte[]]::new($ByteCount)
    [Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    return [Convert]::ToHexString($bytes).ToLowerInvariant()
}

function Invoke-Docker {
    param(
        [Parameter(Mandatory)][string[]]$Arguments,
        [Parameter(Mandatory)][string]$Operation
    )

    & docker @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Operation failed with exit code $LASTEXITCODE."
    }
}

function Get-AvailablePort {
    $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
    try {
        $listener.Start()
        return ([Net.IPEndPoint]$listener.LocalEndpoint).Port
    }
    finally {
        $listener.Stop()
    }
}

function Wait-SmokeStack {
    param(
        [Parameter(Mandatory)][string[]]$ComposeArguments,
        [Parameter(Mandatory)][int]$Timeout
    )

    $deadline = (Get-Date).AddSeconds($Timeout)
    $services = @('postgres', 'redis', 'backend', 'frontend')
    do {
        $migrationId = ([string](& docker @ComposeArguments ps --all --quiet migrations)).Trim()
        if ($LASTEXITCODE -ne 0) { throw 'Unable to inspect the migration container.' }
        $migrationStatus = 'missing'
        $migrationExitCode = $null
        if ($migrationId) {
            $migrationStatus = ([string](docker inspect --format '{{.State.Status}}' $migrationId)).Trim()
            $migrationExitCode = ([string](docker inspect --format '{{.State.ExitCode}}' $migrationId)).Trim()
            if ($migrationStatus -eq 'exited' -and $migrationExitCode -ne '0') {
                throw "Migration service exited with code $migrationExitCode."
            }
        }

        $allHealthy = $true
        foreach ($service in $services) {
            $containerId = ([string](& docker @ComposeArguments ps --quiet $service)).Trim()
            if ($LASTEXITCODE -ne 0) { throw "Unable to inspect service '$service'." }
            $status = if ($containerId) {
                ([string](docker inspect --format `
                    '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' `
                    $containerId)).Trim()
            }
            else {
                'missing'
            }
            if ($status -ne 'healthy') { $allHealthy = $false }
        }

        if ($migrationStatus -eq 'exited' -and $migrationExitCode -eq '0' -and $allHealthy) {
            return
        }
        if ((Get-Date) -ge $deadline) {
            throw 'The isolated Compose stack did not become healthy before the timeout.'
        }
        Start-Sleep -Milliseconds 1000
    } while ($true)
}

function New-SmokeClient {
    param([Parameter(Mandatory)][Uri]$BaseUri)

    $handler = [Net.Http.HttpClientHandler]::new()
    $handler.UseCookies = $true
    $handler.UseProxy = $false
    $handler.CookieContainer = [Net.CookieContainer]::new()
    # Caddy creates a per-run internal CA inside the ephemeral volume. The smoke
    # validates HTTPS transport and cookie behavior without trusting that CA globally.
    $handler.ServerCertificateCustomValidationCallback =
        [Net.Http.HttpClientHandler]::DangerousAcceptAnyServerCertificateValidator
    $client = [Net.Http.HttpClient]::new($handler)
    $client.BaseAddress = $BaseUri
    $client.Timeout = [TimeSpan]::FromSeconds(15)
    return [pscustomobject]@{ Handler = $handler; Client = $client }
}

function Invoke-SmokeRequest {
    param(
        [Parameter(Mandatory)][Net.Http.HttpClient]$Client,
        [Parameter(Mandatory)][string]$Method,
        [Parameter(Mandatory)][string]$Path,
        [hashtable]$Headers = @{},
        [object]$Body = $null
    )

    $request = [Net.Http.HttpRequestMessage]::new([Net.Http.HttpMethod]::new($Method), $Path)
    try {
        foreach ($name in $Headers.Keys) {
            if (-not $request.Headers.TryAddWithoutValidation($name, [string]$Headers[$name])) {
                throw "Unable to add required HTTP header '$name'."
            }
        }
        if ($null -ne $Body) {
            $json = $Body | ConvertTo-Json -Compress -Depth 8
            $request.Content = [Net.Http.StringContent]::new(
                $json, [Text.Encoding]::UTF8, 'application/json')
        }
        $response = $Client.SendAsync($request).GetAwaiter().GetResult()
        try {
            $content = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
            $setCookie = if ($response.Headers.Contains('Set-Cookie')) {
                @($response.Headers.GetValues('Set-Cookie')) -join ', '
            }
            else {
                ''
            }
            return [pscustomobject]@{
                StatusCode = [int]$response.StatusCode
                Body = $content
                SetCookie = $setCookie
            }
        }
        finally {
            $response.Dispose()
        }
    }
    finally {
        $request.Dispose()
    }
}

function Assert-Status {
    param(
        [Parameter(Mandatory)]$Response,
        [Parameter(Mandatory)][int]$Expected,
        [Parameter(Mandatory)][string]$Stage
    )

    if ($Response.StatusCode -eq $Expected) { return }
    $errorCode = 'unavailable'
    try {
        $document = $Response.Body | ConvertFrom-Json
        if ($document.error.code) { $errorCode = $document.error.code }
    }
    catch {
        $errorCode = 'unparseable'
    }
    throw "$Stage expected HTTP $Expected but received $($Response.StatusCode) ($errorCode)."
}

function Assert-ErrorCode {
    param(
        [Parameter(Mandatory)]$Response,
        [Parameter(Mandatory)][string]$Expected,
        [Parameter(Mandatory)][string]$Stage
    )

    $document = $Response.Body | ConvertFrom-Json
    if ($document.error.code -ne $Expected) {
        throw "$Stage returned an unexpected application error code."
    }
}

function Write-Pass {
    param([Parameter(Mandatory)][string]$Message)
    Write-Host "PASS: $Message" -ForegroundColor Green
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw 'Docker CLI was not found.'
}
foreach ($requiredFile in @($BaseComposePath, $SmokeComposePath, $HelperPath)) {
    if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
        throw "Required smoke asset is missing: $requiredFile"
    }
}

$runId = (Get-RandomHex -ByteCount 8)
$ProjectName = "sentinelai-smoke-$runId"
$DatabaseName = "sentinelai_smoke_$runId"
$HttpPort = Get-AvailablePort
do { $HttpsPort = Get-AvailablePort } while ($HttpsPort -eq $HttpPort)
$BaseUri = [Uri]"https://127.0.0.1:$HttpsPort/"
$TrustedOrigin = $BaseUri.AbsoluteUri.TrimEnd('/')
$PasswordA = "Aa1!$(Get-RandomHex -ByteCount 24)"
$PasswordB = "Bb2!$(Get-RandomHex -ByteCount 24)"
$EmailA = "smoke-admin-$runId@example.com"
$EmailB = "smoke-viewer-$runId@example.com"
$EnvironmentValues = @{
    COMPOSE_PROJECT_NAME = $ProjectName
    SMOKE_PROJECT_NAME = $ProjectName
    SMOKE_HTTPS_PORT = [string]$HttpsPort
    FRONTEND_PORT = [string]$HttpPort
    ENVIRONMENT = 'test'
    LOG_LEVEL = 'INFO'
    CORS_ORIGINS = $TrustedOrigin
    TRUSTED_ORIGINS = $TrustedOrigin
    CSRF_HEADER_NAME = $CsrfHeaderName
    POSTGRES_DB = $DatabaseName
    POSTGRES_USER = 'sentinelai_migrator'
    POSTGRES_PASSWORD = Get-RandomHex -ByteCount 32
    POSTGRES_RUNTIME_USER = 'sentinelai_runtime'
    POSTGRES_RUNTIME_PASSWORD = Get-RandomHex -ByteCount 32
    REDIS_PASSWORD = Get-RandomHex -ByteCount 32
}
$OriginalEnvironment = @{}
foreach ($name in $EnvironmentValues.Keys) {
    $OriginalEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
    [Environment]::SetEnvironmentVariable($name, $EnvironmentValues[$name], 'Process')
}

$ComposeArguments = @(
    'compose', '--project-name', $ProjectName,
    '--file', $BaseComposePath,
    '--file', $SmokeComposePath
)
$PrimaryFailure = $null
$CleanupFailure = $null
$StackMayExist = $false
$AdminClient = $null
$ViewerClient = $null
$StaleClient = $null
$CurrentStage = 'initialization'

try {
    $CurrentStage = 'Docker engine verification'
    Invoke-Docker -Arguments @('info') -Operation $CurrentStage
    $CurrentStage = 'Compose configuration validation'
    Invoke-Docker -Arguments ($ComposeArguments + @('config', '--quiet')) -Operation $CurrentStage

    $CurrentStage = 'isolated Compose startup'
    $StackMayExist = $true
    Invoke-Docker -Arguments ($ComposeArguments + @('up', '--detach', '--build')) `
        -Operation $CurrentStage
    Wait-SmokeStack -ComposeArguments $ComposeArguments -Timeout $TimeoutSeconds
    Write-Pass 'PostgreSQL, Redis, migrations, backend and HTTPS frontend are healthy.'

    $CurrentStage = 'temporary identity seed'
    $seedPayload = @{
        a_organization_name = "Smoke Organization A $runId"
        a_organization_slug = "smoke-org-a-$runId"
        a_email = $EmailA
        a_display_name = 'Smoke Administrator A'
        a_password = $PasswordA
        b_organization_name = "Smoke Organization B $runId"
        b_organization_slug = "smoke-org-b-$runId"
        b_email = $EmailB
        b_display_name = 'Smoke Viewer B'
        b_password = $PasswordB
    } | ConvertTo-Json -Compress
    $seedArguments = $ComposeArguments + @(
        'run', '--rm', '-T', '--no-deps',
        '--volume', "${HelperPath}:/tmp/authenticated-compose-smoke-helper.py:ro",
        '--env', 'PYTHONPATH=/app',
        '--entrypoint', 'python', 'backend',
        '/tmp/authenticated-compose-smoke-helper.py', 'seed'
    )
    $seedOutput = @($seedPayload | & docker @seedArguments)
    $seedPayload = $null
    if ($LASTEXITCODE -ne 0) { throw 'Ephemeral identity seed failed.' }
    $seedLine = $seedOutput | Where-Object { $_ -like 'SMOKE_SEED_RESULT=*' } |
        Select-Object -Last 1
    if (-not $seedLine) { throw 'Ephemeral identity seed returned no result marker.' }
    $seed = $seedLine.Substring('SMOKE_SEED_RESULT='.Length) | ConvertFrom-Json
    $OrganizationA = [string]$seed.organization_a_id
    $OrganizationB = [string]$seed.organization_b_id
    $UserA = [string]$seed.user_a_id
    Write-Pass 'Two isolated organizations and two temporary identities were created.'

    $AdminClient = New-SmokeClient -BaseUri $BaseUri
    $CurrentStage = 'unauthenticated /me rejection'
    $response = Invoke-SmokeRequest -Client $AdminClient.Client -Method GET -Path '/api/v1/me'
    Assert-Status $response 401 $CurrentStage
    Assert-ErrorCode $response 'UNAUTHENTICATED' $CurrentStage

    $CurrentStage = 'invalid login rejection'
    $response = Invoke-SmokeRequest -Client $AdminClient.Client -Method POST `
        -Path '/api/v1/session' `
        -Body @{ email = $EmailA; password = 'Invalid-smoke-password-2026!' }
    Assert-Status $response 401 $CurrentStage
    Assert-ErrorCode $response 'INVALID_CREDENTIALS' $CurrentStage
    Write-Pass 'Invalid login and unauthenticated /me were rejected.'

    $CurrentStage = 'successful deployed login'
    $response = Invoke-SmokeRequest -Client $AdminClient.Client -Method POST `
        -Path '/api/v1/session' -Body @{ email = $EmailA; password = $PasswordA }
    Assert-Status $response 200 $CurrentStage
    $login = $response.Body | ConvertFrom-Json
    $cookie = $AdminClient.Handler.CookieContainer.GetCookies($BaseUri)[$CookieName]
    if (-not $cookie) { throw 'Login did not establish the expected session cookie.' }
    if (-not $cookie.Secure -or -not $cookie.HttpOnly) {
        throw 'Session cookie is missing Secure or HttpOnly.'
    }
    if ($response.SetCookie -notmatch '(?i)(^|;\s*)SameSite=Lax(;|$)' -or
        $response.SetCookie -notmatch '(?i)(^|;\s*)Path=/(;|$)' -or
        $response.SetCookie -match '(?i)(^|;\s*)Domain=') {
        throw 'Session cookie violates the __Host cookie contract.'
    }
    if (-not $login.csrf_token -or $login.expires_in -le 0) {
        throw 'Login response omitted the CSRF or expiry contract.'
    }
    if ($response.Body.Contains($cookie.Value) -or
        $login.PSObject.Properties.Name -contains 'session_token') {
        throw 'Opaque session material was exposed in the login response body.'
    }
    $StaleSessionToken = $cookie.Value
    $CsrfA = [string]$login.csrf_token
    Write-Pass 'Login established an opaque Secure/HttpOnly/SameSite=Lax __Host cookie.'

    $CurrentStage = 'authenticated /me'
    $response = Invoke-SmokeRequest -Client $AdminClient.Client -Method GET -Path '/api/v1/me'
    Assert-Status $response 200 $CurrentStage
    $me = $response.Body | ConvertFrom-Json
    if ($me.id -ne $UserA -or $me.email -ne $EmailA -or
        $me.active_organization_id -ne $OrganizationA -or
        $me.roles -notcontains 'platform_admin') {
        throw 'Authenticated /me returned an unexpected identity or tenant context.'
    }
    Write-Pass 'Authenticated /me returned the expected user, role and active organization.'

    $CurrentStage = 'negative CSRF controls'
    $originHeader = @{ Origin = $TrustedOrigin }
    $response = Invoke-SmokeRequest -Client $AdminClient.Client -Method POST `
        -Path '/api/v1/session/rotate' -Headers $originHeader
    Assert-Status $response 403 'missing CSRF rejection'
    Assert-ErrorCode $response 'CSRF_VALIDATION_FAILED' 'missing CSRF rejection'
    $response = Invoke-SmokeRequest -Client $AdminClient.Client -Method POST `
        -Path '/api/v1/session/rotate' `
        -Headers @{ Origin = $TrustedOrigin; $CsrfHeaderName = 'incorrect-csrf-value' }
    Assert-Status $response 403 'incorrect CSRF rejection'
    Assert-ErrorCode $response 'CSRF_VALIDATION_FAILED' 'incorrect CSRF rejection'
    $response = Invoke-SmokeRequest -Client $AdminClient.Client -Method POST `
        -Path '/api/v1/session/rotate' `
        -Headers @{ Origin = 'https://hostile.example'; $CsrfHeaderName = $CsrfA }
    Assert-Status $response 403 'untrusted Origin rejection'
    Assert-ErrorCode $response 'CSRF_VALIDATION_FAILED' 'untrusted Origin rejection'
    Write-Pass 'Missing CSRF, incorrect CSRF and untrusted Origin were rejected.'

    $CurrentStage = 'authorized tenant mutation'
    $authorizedHeaders = @{
        Origin = $TrustedOrigin
        $CsrfHeaderName = $CsrfA
        'If-Match' = '1'
    }
    $response = Invoke-SmokeRequest -Client $AdminClient.Client -Method PATCH `
        -Path "/api/v1/organizations/$OrganizationA" -Headers $authorizedHeaders `
        -Body @{ name = "Authenticated Smoke Organization A $runId" }
    Assert-Status $response 200 $CurrentStage
    $updatedOrganization = $response.Body | ConvertFrom-Json
    if ($updatedOrganization.id -ne $OrganizationA -or $updatedOrganization.version -ne 2) {
        throw 'Authorized organization mutation returned unexpected state.'
    }
    Write-Pass 'Trusted Origin plus valid CSRF completed an authorized tenant mutation.'

    $CurrentStage = 'HTTP tenant isolation'
    $response = Invoke-SmokeRequest -Client $AdminClient.Client -Method GET `
        -Path '/api/v1/organizations'
    Assert-Status $response 200 $CurrentStage
    $visibleOrganizations = @((($response.Body | ConvertFrom-Json).items))
    if ($visibleOrganizations.Count -ne 1 -or $visibleOrganizations[0].id -ne $OrganizationA) {
        throw 'Organization listing crossed the active tenant boundary.'
    }
    $response = Invoke-SmokeRequest -Client $AdminClient.Client -Method GET `
        -Path "/api/v1/organizations/$OrganizationB"
    Assert-Status $response 404 'cross-tenant read rejection'
    Assert-ErrorCode $response 'ORGANIZATION_NOT_FOUND' 'cross-tenant read rejection'
    $response = Invoke-SmokeRequest -Client $AdminClient.Client -Method PATCH `
        -Path "/api/v1/organizations/$OrganizationB" -Headers $authorizedHeaders `
        -Body @{ name = 'Forbidden cross-tenant mutation' }
    Assert-Status $response 404 'cross-tenant mutation rejection'
    Assert-ErrorCode $response 'ORGANIZATION_NOT_FOUND' 'cross-tenant mutation rejection'
    Write-Pass 'Cross-tenant HTTP reads and mutations were invisible/rejected.'

    $CurrentStage = 'deployed audit API evidence'
    $response = Invoke-SmokeRequest -Client $AdminClient.Client -Method GET `
        -Path '/api/v1/security-audit-events?action=organization.updated'
    Assert-Status $response 200 $CurrentStage
    $auditItems = @((($response.Body | ConvertFrom-Json).items))
    $auditEvent = $auditItems | Where-Object {
        $_.action -eq 'organization.updated' -and $_.resource_id -eq $OrganizationA -and
        $_.actor_user_id -eq $UserA -and $_.outcome -eq 'success'
    } | Select-Object -First 1
    if (-not $auditEvent -or $auditEvent.hash_version -ne 3 -or
        ([string]$auditEvent.event_hash).Length -ne 64) {
        throw 'Deployed audit API did not expose the expected chained event.'
    }
    Write-Pass 'Authenticated mutation produced the expected audit API evidence.'

    $CurrentStage = 'secondary identity and RBAC denial'
    $ViewerClient = New-SmokeClient -BaseUri $BaseUri
    $response = Invoke-SmokeRequest -Client $ViewerClient.Client -Method POST `
        -Path '/api/v1/session' -Body @{ email = $EmailB; password = $PasswordB }
    Assert-Status $response 200 'secondary identity login'
    $viewerLogin = $response.Body | ConvertFrom-Json
    $response = Invoke-SmokeRequest -Client $ViewerClient.Client -Method GET -Path '/api/v1/me'
    Assert-Status $response 200 'secondary identity /me'
    $viewerMe = $response.Body | ConvertFrom-Json
    if ($viewerMe.active_organization_id -ne $OrganizationB -or
        $viewerMe.roles -notcontains 'viewer') {
        throw 'Secondary identity did not resolve to Organization B as viewer.'
    }
    $response = Invoke-SmokeRequest -Client $ViewerClient.Client -Method PATCH `
        -Path "/api/v1/organizations/$OrganizationB" `
        -Headers @{
            Origin = $TrustedOrigin
            $CsrfHeaderName = [string]$viewerLogin.csrf_token
            'If-Match' = '1'
        } -Body @{ name = 'Viewer must not update this organization' }
    Assert-Status $response 403 'viewer authorization rejection'
    Assert-ErrorCode $response 'FORBIDDEN' 'viewer authorization rejection'
    Write-Pass 'Viewer identity resolved to Organization B and was denied owner mutation.'

    $CurrentStage = 'runtime PostgreSQL RLS and audit verification'
    $verifyArguments = $ComposeArguments + @(
        'run', '--rm', '-T', '--no-deps',
        '--volume', "${HelperPath}:/tmp/authenticated-compose-smoke-helper.py:ro",
        '--env', 'PYTHONPATH=/app',
        '--entrypoint', 'python', 'backend',
        '/tmp/authenticated-compose-smoke-helper.py', 'verify',
        '--organization-a', $OrganizationA,
        '--organization-b', $OrganizationB,
        '--user-a', $UserA
    )
    $verifyOutput = @(& docker @verifyArguments)
    if ($LASTEXITCODE -ne 0 -or
        -not ($verifyOutput | Where-Object { $_ -eq 'SMOKE_DATABASE_EVIDENCE=PASS' })) {
        throw 'Runtime PostgreSQL RLS/audit evidence verification failed.'
    }
    Write-Pass 'Runtime role tenant visibility, NOBYPASSRLS and audit immutability passed.'

    $CurrentStage = 'logout and stale-session rejection'
    $response = Invoke-SmokeRequest -Client $AdminClient.Client -Method DELETE `
        -Path '/api/v1/session' `
        -Headers @{ Origin = $TrustedOrigin; $CsrfHeaderName = $CsrfA }
    Assert-Status $response 204 $CurrentStage
    if ($response.SetCookie -notmatch '(?i)Max-Age=0' -or
        $response.SetCookie -notmatch '(?i)(^|;\s*)Secure(;|$)' -or
        $response.SetCookie -notmatch '(?i)(^|;\s*)HttpOnly(;|$)') {
        throw 'Logout did not clear the session cookie with the secure contract.'
    }
    $StaleClient = New-SmokeClient -BaseUri $BaseUri
    $response = Invoke-SmokeRequest -Client $StaleClient.Client -Method GET `
        -Path '/api/v1/me' -Headers @{ Cookie = "$CookieName=$StaleSessionToken" }
    Assert-Status $response 401 'stale post-logout session rejection'
    Assert-ErrorCode $response 'UNAUTHENTICATED' 'stale post-logout session rejection'
    Write-Pass 'Logout revoked the session; the stale opaque cookie was rejected.'
}
catch {
    $PrimaryFailure = $_
    Write-Warning "Authenticated Compose smoke failed during: $CurrentStage"
    if ($StackMayExist) {
        & docker @($ComposeArguments + @('ps', '--all'))
        & docker @($ComposeArguments + @('logs', '--tail', '80',
            'migrations', 'backend', 'frontend'))
    }
}
finally {
    foreach ($bundle in @($AdminClient, $ViewerClient, $StaleClient)) {
        if ($null -ne $bundle) {
            $bundle.Client.Dispose()
            $bundle.Handler.Dispose()
        }
    }
    if ($StackMayExist) {
        try {
            Invoke-Docker -Arguments ($ComposeArguments + @(
                'down', '--volumes', '--remove-orphans', '--timeout', '20')) `
                -Operation 'ephemeral Compose cleanup'
            $remainingContainers = @(& docker ps --all --quiet `
                --filter "label=com.docker.compose.project=$ProjectName")
            $remainingNetworks = @(& docker network ls --quiet `
                --filter "label=com.docker.compose.project=$ProjectName")
            $remainingVolumes = @(& docker volume ls --quiet `
                --filter "label=com.docker.compose.project=$ProjectName")
            if ($remainingContainers.Count -or $remainingNetworks.Count -or
                $remainingVolumes.Count) {
                throw 'Ephemeral Compose resources remain after cleanup.'
            }
            Write-Pass 'Ephemeral containers, networks and volumes were removed.'
        }
        catch {
            $CleanupFailure = $_
        }
    }
    foreach ($name in $EnvironmentValues.Keys) {
        [Environment]::SetEnvironmentVariable($name, $OriginalEnvironment[$name], 'Process')
    }
    $PasswordA = $null
    $PasswordB = $null
    $StaleSessionToken = $null
    $EnvironmentValues = $null
}

if ($null -ne $PrimaryFailure) { throw $PrimaryFailure.Exception }
if ($null -ne $CleanupFailure) { throw $CleanupFailure.Exception }
Write-Host 'AUTHENTICATED COMPOSE SMOKE: PASS' -ForegroundColor Green
