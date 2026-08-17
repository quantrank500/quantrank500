# Assembles dist\prod-bundle\ — the dark prod VM payload, ready to scp.
#     .\scripts\make_prod_bundle.ps1
# No dumps, ever: prod is born pristine and its first record is a real one.
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$bundle = Join-Path $repo "dist\prod-bundle"

Remove-Item -Recurse -Force $bundle -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force "$bundle\app" | Out-Null

# deploy files
Copy-Item "$repo\deploy\vm-prod\docker-compose.yml" $bundle
Copy-Item "$repo\deploy\vm\nginx.conf" $bundle
Copy-Item "$repo\deploy\vm-prod\deploy.sh" $bundle
Copy-Item "$repo\deploy\vm-prod\env.example" $bundle

# frontend (served static by nginx)
Copy-Item -Recurse "$repo\frontend" "$bundle\frontend"

# app build context (API + worker image)
Copy-Item "$repo\Dockerfile" "$bundle\app"
Copy-Item "$repo\pyproject.toml" "$bundle\app"
Copy-Item -Recurse "$repo\src" "$bundle\app\src"
Copy-Item -Recurse "$repo\scripts" "$bundle\app\scripts"

# LF endings for anything bash runs
$sh = Join-Path $bundle "deploy.sh"
(Get-Content $sh -Raw) -replace "`r`n", "`n" | Set-Content $sh -NoNewline -Encoding ascii

$size = (Get-ChildItem -Recurse $bundle | Measure-Object -Property Length -Sum).Sum / 1MB
Write-Host ("Bundle ready: {0}  ({1:N1} MB)" -f $bundle, $size)
Write-Host "Ship it:  scp -r `"$bundle`" ubuntu@<PROD_IP>:~/"
Write-Host "Then on the VM:  cd ~/prod-bundle && bash deploy.sh"
