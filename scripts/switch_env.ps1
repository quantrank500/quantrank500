# Switch the running stack between local environments (dev | demo).
#
#     .\scripts\switch_env.ps1 demo
#     .\scripts\switch_env.ps1 dev
#
# Recreates api + worker with the environment's database, restarts nginx
# (it must re-resolve the api container's address), then confirms what the
# API actually reports. Deliberately refuses "prod": production is defined
# at M6 and will never be one command away from a demo switch.

param(
    [Parameter(Mandatory = $true)]
    [string]$Environment
)

$repo = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $repo "envs\$Environment.env"

if ($Environment -eq "prod") {
    Write-Error "Refusing: prod is not switchable from this script (spec M6)."
    exit 1
}
if (-not (Test-Path $envFile)) {
    $known = (Get-ChildItem (Join-Path $repo "envs") -Filter "*.env").BaseName -join ", "
    Write-Error "Unknown environment '$Environment'. Known: $known"
    exit 1
}

Write-Host "Switching stack to '$Environment'..."
docker compose --project-directory $repo --env-file $envFile up -d api worker
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
docker compose --project-directory $repo restart nginx | Out-Null

Start-Sleep -Seconds 3
try {
    $info = Invoke-RestMethod -Uri "http://localhost:8000/" -TimeoutSec 15
    Write-Host "API now serving environment: $($info.environment)"
    if ($info.environment -ne $Environment) {
        Write-Warning "API reports '$($info.environment)' — containers may still be starting; check again in a few seconds."
    }
}
catch {
    Write-Warning "Could not reach the API yet ($_). Give it a few seconds, then check http://localhost:8000/"
}
