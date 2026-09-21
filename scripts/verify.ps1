[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$RepositoryRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath (Join-Path $RepositoryRoot 'frontend')
try {
    pnpm install --frozen-lockfile
    if ($LASTEXITCODE -ne 0) { throw 'Frontend install failed.' }
    pnpm lint
    if ($LASTEXITCODE -ne 0) { throw 'Frontend lint failed.' }
    pnpm typecheck
    if ($LASTEXITCODE -ne 0) { throw 'Frontend typecheck failed.' }
    pnpm build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
    pnpm audit --prod --audit-level=high
    if ($LASTEXITCODE -ne 0) { throw 'Frontend audit failed.' }
}
finally { Pop-Location }
& (Join-Path $PSScriptRoot 'backend-quality.ps1')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host 'All local frontend and ephemeral backend gates passed.'
exit 0
