# Assembles dist\demo-bundle\ — everything the VM needs, ready to scp.
#     .\scripts\make_demo_bundle.ps1
# Re-run any time to refresh (dumps are re-exported from the live demo db).
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$bundle = Join-Path $repo "dist\demo-bundle"

Remove-Item -Recurse -Force $bundle -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force "$bundle\dumps", "$bundle\app" | Out-Null

# deploy files
Copy-Item "$repo\deploy\vm\docker-compose.yml" $bundle
Copy-Item "$repo\deploy\vm\nginx.conf" $bundle
Copy-Item "$repo\deploy\vm\deploy.sh" $bundle
Copy-Item "$repo\deploy\vm\env.example" $bundle

# frontend (served static by nginx)
Copy-Item -Recurse "$repo\frontend" "$bundle\frontend"

# app build context (API + worker image)
Copy-Item "$repo\Dockerfile" "$bundle\app"
Copy-Item "$repo\pyproject.toml" "$bundle\app"
Copy-Item -Recurse "$repo\src" "$bundle\app\src"
Copy-Item -Recurse "$repo\scripts" "$bundle\app\scripts"

# dumps: demo app db + daily prices only (minute bars NEVER leave this PC)
Write-Host "Exporting demo database dump..."
docker exec quantrank500-db pg_dump -U quantrank quantrank500_demo | `
    Out-File -Encoding utf8NoBOM "$bundle\dumps\demo_app.sql"
Write-Host "Exporting lake.daily_prices dump..."
docker exec quantrank500-db pg_dump -U quantrank -d quantrank500 -t lake.daily_prices | `
    Out-File -Encoding utf8NoBOM "$bundle\dumps\lake_daily.sql"

$appSize = (Get-ChildItem -Recurse $bundle | Measure-Object -Property Length -Sum).Sum / 1MB
Write-Host ""
Write-Host ("Bundle ready: {0}  ({1:N1} MB)" -f $bundle, $appSize)
Write-Host "Ship it:  scp -i `$env:USERPROFILE\.ssh\qr500_vm -r `"$bundle`" ubuntu@<VM_IP>:~/"
Write-Host "Then on the VM:  cd ~/demo-bundle && bash deploy.sh"
