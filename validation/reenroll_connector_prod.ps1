# Re-enroll the connector against PRODUCTION for Vighnaharta Agro Chemicals.
# Run this yourself in your own PowerShell window — it asks for your own
# login, never shares it with anyone else. Requires you to be an `owner`
# on the company.

$ErrorActionPreference = 'Stop'
$Backend = 'https://books.gcwealthguru.com'
$CompanyId = '32a51be2-13f5-4b75-a67e-0f1d77b3121f'  # Vighnaharta Agro Chemicals

$email = Read-Host 'Email'
$securePw = Read-Host 'Password' -AsSecureString
$pw = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePw))

Write-Host 'Logging in...'
$login = Invoke-RestMethod -Uri "$Backend/api/v1/auth/login" -Method Post `
    -Body @{ username = $email; password = $pw } -ContentType 'application/x-www-form-urlencoded'
$access = $login.access_token
if (-not $access) { throw 'Login did not return an access_token.' }

Write-Host 'Issuing enrollment code...'
$idemKey = [guid]::NewGuid().ToString()
$codeResp = Invoke-RestMethod -Uri "$Backend/api/v1/connector/enrollment-codes" -Method Post `
    -Headers @{ Authorization = "Bearer $access"; 'X-Company-ID' = $CompanyId; 'Idempotency-Key' = $idemKey } `
    -Body '{}' -ContentType 'application/json'
Write-Host ("Code issued, expires " + $codeResp.expires_at)

Write-Host 'Exchanging code for a connector token...'
$enrollResp = Invoke-RestMethod -Uri "$Backend/api/v1/connector/enroll" -Method Post `
    -Body (@{ code = $codeResp.code } | ConvertTo-Json) -ContentType 'application/json'

$envPath = "H:\Accounting Project\connector\dist\.env"
@"
CONNECTOR_TOKEN=$($enrollResp.connector_token)
CONNECTOR_COMPANY_ID=$($enrollResp.company_id)
BACKEND_WS_URL=wss://books.gcwealthguru.com/api/v1/connector/ws
TALLY_HOST=localhost
TALLY_PORT=9000
LOG_LEVEL=INFO
"@ | Set-Content -Path $envPath -Encoding UTF8 -NoNewline

Write-Host "Done. connector_id=$($enrollResp.connector_id), expires_in_days=$($enrollResp.expires_in_days)"
Write-Host "Written to $envPath"
