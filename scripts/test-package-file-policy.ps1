[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'package-file-policy.ps1')
$Denied = @('.env', 'backend/.env.local', 'x/opaque.evidence', 'x/lab-1.kek',
    'x/stage-0123456789abcdef.tmp', 'x/.evidence.lock', 'evidence-data/opaque',
    'x/evidence-keys/opaque', 'x/evidence-storage/opaque', 'node_modules/a.js',
    'coverage/data', '.coverage', 'coverage.json', 'x/private-key.json',
    'x/recovery-codes.txt', 'x/credentials.json', 'x/data.db', 'x/a.pem',
    '../escape', 'C:\outside\file', 'x/.venv/file', 'x/build/a', 'x/.cache/a',
    'x/.evidence.coordinator.lock', 'audit-results/source-hashes.json',
    'audit-results/stage3-fixes-validate.ps1', 'nested/audit-results/report.txt')
$Allowed = @('.env.example', 'backend/app/evidence/storage.py',
    'backend/storage_tests/test_contract_crypto.py', 'docs/engineering/ADR_009_LAB_STORAGE.md')
foreach ($Path in $Denied) {
    if (Test-ClosurePackagePath $Path) { throw "Unsafe package inclusion: $Path" }
}
foreach ($Path in $Allowed) {
    if (-not (Test-ClosurePackagePath $Path)) { throw "Source unexpectedly excluded: $Path" }
}
Write-Host "Package policy PASS: $($Denied.Count) denied, $($Allowed.Count) source paths retained; no ZIP created."
