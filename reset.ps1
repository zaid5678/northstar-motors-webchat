$ErrorActionPreference = "Stop"

Invoke-RestMethod `
  -Method Post `
  -Headers @{ "X-Admin-Key" = "northstar-local-admin" } `
  -Uri "http://localhost:4010/admin/api/reset"

Write-Host "Northstar dealership platform reset."

