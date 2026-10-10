# Dot-sourced only by authenticated-compose-smoke.ps1 inside its isolated project.
# Test identities do not constitute an acceptance by two real people.
if (-not $ControlReviews -or $ProjectName -notmatch '^sentinelai-smoke-[a-f0-9]+$') {
    throw 'Control review smoke requires the owned ephemeral parent harness.'
}
$ReviewHeaders = @()
foreach ($account in $ReviewAccounts) {
    $bundle = New-SmokeClient -BaseUri $BaseUri
    $ReviewClients += $bundle
    $response = Invoke-SmokeRequest -Client $bundle.Client -Method POST -Path '/api/v1/session' -Body $account
    Assert-Status $response 200 'reviewer login'
    $ReviewHeaders += @{ $CsrfHeaderName = [string]($response.Body | ConvertFrom-Json).csrf_token
        Origin = $TrustedOrigin; 'If-Match' = '1'; 'Idempotency-Key' = 'review-decision' }
}
$reviewPath = "/api/v1/assets/$($asset.id)/control-reviews"
$requestBody = @{ evidence_id = $evidenceId; evidence_version = 1 }
$controlHeaders = @{ Origin = $TrustedOrigin; $CsrfHeaderName = $evidenceCsrf
    'If-Match' = '1'; 'Idempotency-Key' = 'control-request' }
$response = Invoke-SmokeRequest -Client $EvidenceClient.Client -Method POST -Path $reviewPath `
    -Headers @{ 'If-Match' = '1'; 'Idempotency-Key' = 'missing-csrf' } -Body $requestBody
Assert-Status $response 403 'review request CSRF'
$response = Invoke-SmokeRequest -Client $EvidenceClient.Client -Method POST -Path $reviewPath `
    -Headers $controlHeaders -Body $requestBody
Assert-Status $response 201 'presenter A request'
$review = $response.Body | ConvertFrom-Json
$decisionPath = "/api/v1/control-reviews/$($review.id)/decisions"
$judgment = @{ decision = 'approved'; checklist_version = 1; reason_code = 'control_confirmed'
    checklist = @{ console_identity = 'confirmed'; inventory_match = 'confirmed'; administrative_control = 'confirmed' } }
$response = Invoke-SmokeRequest -Client $EvidenceClient.Client -Method POST -Path $decisionPath `
    -Headers $controlHeaders -Body $judgment
Assert-Status $response 409 'self approval rejected'
Assert-ErrorCode $response 'CONTROL_SEPARATION_REQUIRED' 'self approval'
$response = Invoke-SmokeRequest -Client $ReviewClients[0].Client -Method POST -Path $decisionPath `
    -Headers $ReviewHeaders[0] -Body $judgment
Assert-Status $response 409 'read receipt required'
Assert-ErrorCode $response 'CONTROL_READING_REQUIRED' 'read receipt'
foreach ($bundle in $ReviewClients) {
    $response = Invoke-SmokeRequest -Client $bundle.Client -Method GET -Path "$evidencePath/$evidenceId/content"
    Assert-Status $response 200 'reviewer authorized evidence read'
}
$response = Invoke-SmokeRequest -Client $ReviewClients[0].Client -Method POST -Path $decisionPath `
    -Headers $ReviewHeaders[0] -Body $judgment
Assert-Status $response 201 'reviewer B decision'
if (($response.Body | ConvertFrom-Json).state -ne 'approved') { throw 'Approval projection missing.' }
$response = Invoke-SmokeRequest -Client $ReviewClients[1].Client -Method POST -Path $decisionPath `
    -Headers $ReviewHeaders[1] -Body $judgment
Assert-Status $response 409 'reviewer C stale decision'
Assert-ErrorCode $response 'VERSION_CONFLICT' 'reviewer C conflict'
$response = Invoke-SmokeRequest -Client $ReviewClients[0].Client -Method POST -Path $decisionPath `
    -Headers $ReviewHeaders[0] -Body $judgment
Assert-Status $response 201 'decision replay'
if ($response.Replayed -ne 'true') { throw 'Decision replay not recognized.' }
foreach ($path in @($reviewPath, "/api/v1/control-reviews/$($review.id)", "/api/v1/assets/$($asset.id)/control-status")) {
    $response = Invoke-SmokeRequest -Client $EvidenceClient.Client -Method GET -Path $path
    Assert-Status $response 200 'review read'
    if ($response.CacheControl -ne 'no-store') { throw 'Review response allows caching.' }
}
$current = $response.Body | ConvertFrom-Json
if (-not $current.valid -or -not $current.checked_at -or $current.ownership_status -ne 'unverified') {
    throw 'Fresh technical validity or ownership invariant failed.'
}
$response = Invoke-SmokeRequest -Client $ViewerClient.Client -Method GET -Path $reviewPath
Assert-Status $response 403 'foreign viewer cannot consult review history'
$response = Invoke-SmokeRequest -Client $ViewerClient.Client -Method GET -Path "/api/v1/assets/$($asset.id)/control-summary"
Assert-Status $response 404 'foreign tenant summary hidden'
$response = Invoke-SmokeRequest -Client $AdminClient.Client -Method GET -Path "/api/v1/assets/$($asset.id)/control-summary"
Assert-Status $response 200 'platform admin minimal summary only'
$summary = $response.Body | ConvertFrom-Json
if (@($summary.PSObject.Properties).Count -ne 3 -or $summary.review_count -ne 1) { throw 'Summary leaked fields.' }
$ReviewHeaders[1]['If-Match'] = '2'
$ReviewHeaders[1]['Idempotency-Key'] = 'review-revocation'
$response = Invoke-SmokeRequest -Client $ReviewClients[1].Client -Method POST `
    -Path "/api/v1/control-reviews/$($review.id)/revocations" -Headers $ReviewHeaders[1] `
    -Body @{ reason_code = 'confidence_withdrawn' }
Assert-Status $response 201 'reviewer C revocation'
$response = Invoke-SmokeRequest -Client $EvidenceClient.Client -Method GET -Path "/api/v1/assets/$($asset.id)/control-status"
Assert-Status $response 200 'revoked status'
$current = $response.Body | ConvertFrom-Json
if ($current.valid -or 'revoked' -notin $current.reasons) { throw 'Revoked approval remained valid.' }
$controlHeaders['Idempotency-Key'] = 'control-renewal'
$requestBody['renews_review_id'] = $review.id
$response = Invoke-SmokeRequest -Client $EvidenceClient.Client -Method POST -Path $reviewPath `
    -Headers $controlHeaders -Body $requestBody
Assert-Status $response 201 'linked renewal'
$renewal = $response.Body | ConvertFrom-Json
$controlHeaders['Idempotency-Key'] = 'withdraw-renewal'
$response = Invoke-SmokeRequest -Client $EvidenceClient.Client -Method POST `
    -Path "/api/v1/control-reviews/$($renewal.id)/withdrawals" -Headers $controlHeaders `
    -Body @{ reason_code = 'presenter_withdrawal' }
Assert-Status $response 201 'presenter withdraws renewal'
Write-Pass '4B HTTPS: presenter A, reviewers B/C, read receipt, separation, replay, revocation, renewal, withdrawal and two tenants.'
Write-Host 'CONTROL REVIEW HTTPS SMOKE: PASS'
