$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
if (Test-Path -LiteralPath '.env') {
    foreach ($line in Get-Content -LiteralPath '.env' -Encoding UTF8) {
        if ($line -match '^\s*([A-Z][A-Z0-9_]*)=(.*)$') {
            $name = $Matches[1]
            $value = $Matches[2].Trim().Trim('"').Trim("'")
            if (-not [Environment]::GetEnvironmentVariable($name, 'Process')) {
                [Environment]::SetEnvironmentVariable($name, $value, 'Process')
            }
        }
    }
}
if (-not $env:BUSINESS_API_DATABASE) { $env:BUSINESS_API_DATABASE = '.runtime/business-api.sqlite3' }
if (-not $env:BUSINESS_API_HOST) { $env:BUSINESS_API_HOST = '127.0.0.1' }
if (-not $env:BUSINESS_API_PORT) { $env:BUSINESS_API_PORT = '8080' }
if (-not $env:BUSINESS_API_ADMIN_USERNAME) { $env:BUSINESS_API_ADMIN_USERNAME = 'admin' }
if (-not (Test-Path -LiteralPath $env:BUSINESS_API_DATABASE) -and -not $env:BUSINESS_API_ADMIN_PASSWORD) {
    $env:BUSINESS_API_ADMIN_PASSWORD = 'Local-' + [guid]::NewGuid().ToString('N')
    Write-Host "Initial account: $env:BUSINESS_API_ADMIN_USERNAME"
    Write-Host "Initial password (save it now): $env:BUSINESS_API_ADMIN_PASSWORD"
}
Write-Host "Database: $env:BUSINESS_API_DATABASE"
Write-Host "Open http://$($env:BUSINESS_API_HOST):$($env:BUSINESS_API_PORT)"
python -m services.business_api.server
exit $LASTEXITCODE
