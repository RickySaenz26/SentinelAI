[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
# The old scaffold-only uvicorn helper lacked PostgreSQL and migrations.
& (Join-Path $PSScriptRoot 'docker-build.ps1')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& (Join-Path $PSScriptRoot 'docker-up.ps1')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& (Join-Path $PSScriptRoot 'docker-verify.ps1') -TimeoutSeconds 120
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
