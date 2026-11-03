<#
Try OpenPlanner on Windows with Docker Desktop (docs/try-it.md). The same as scripts/try.sh.

    powershell -ExecutionPolicy Bypass -File scripts\try.ps1 [up|status|logs|down|reset] [-NoOpen]

For a server, use scripts/install.sh instead. Every address is on 127.0.0.1; the accounts are
development-only (dev/keycloak/README.md).
#>
param(
    [ValidateSet('up', 'status', 'logs', 'down', 'reset')][string]$Command = 'up',
    [switch]$NoOpen
)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
$ComposeMin = [version]'2.24.0'
$ReadyTimeout = if ($env:READY_TIMEOUT) { [int]$env:READY_TIMEOUT } else { 600 }
$Web = 'http://127.0.0.1:8000'

function Fail([string]$Message) { Write-Error "Error: $Message"; exit 1 }

function Test-Docker {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Fail 'Docker is not installed. Install Docker Desktop: https://docs.docker.com/get-docker/'
    }
    docker info *> $null
    if ($LASTEXITCODE -ne 0) { Fail 'Docker is installed but not running. Start Docker Desktop and run this again.' }
    $short = (docker compose version --short 2>$null)
    if (-not $short) { Fail 'Docker Compose v2 is missing. Update Docker Desktop.' }
    if ([version]($short -replace '^v', '' -replace '[^0-9.].*$', '') -lt $ComposeMin) {
        Fail "Docker Compose $short is too old; $ComposeMin or newer is needed. Update Docker Desktop."
    }
}

function Test-Ports {
    if (docker compose --profile dev ps --quiet 2>$null) { return }
    $busy = foreach ($port in 8000, 8080, 8025) {
        if (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) { $port }
    }
    if ($busy) { Fail "port(s) $($busy -join ', ') on this computer are in use by another program. Stop it and run this again." }
}

function Write-Addresses {
    @"

OpenPlanner is running.

  Book as a participant   $Web/g/demo
  Admin portal            $Web/admin/
  Mail that was "sent"    http://127.0.0.1:8025   (Mailpit: confirmation and reminder emails)

Log in to the portal with a development account; every password is: dev-only-password
  super       everything (start here)         orgadmin    one organisation
  groupadmin  one group                       auditor     read-only audit
  agent       the agent's queue               norole      logged in without rights

Stop:   scripts\try.ps1 down      (keeps your data)
Start over with empty data:   scripts\try.ps1 reset
"@ | Write-Host
}

function Invoke-Up {
    Test-Docker
    Test-Ports
    if (-not (Test-Path .env)) {
        Copy-Item .env.example .env
        Write-Host 'Wrote .env from .env.example (development settings).'
    }
    Write-Host 'Building and starting (the first run downloads and builds for a few minutes)...'
    docker compose --profile dev up --build -d
    if ($LASTEXITCODE -ne 0) { Fail 'docker compose could not start the containers.' }
    $probe = "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=3).status == 200 else 1)"
    $waited = 0
    while ($true) {
        docker compose exec -T web python -c $probe *> $null
        if ($LASTEXITCODE -eq 0) { break }
        $waited += 3
        if ($waited -gt $ReadyTimeout) {
            docker compose --profile dev logs --tail 40 web
            Fail "the application did not become ready within $ReadyTimeout seconds (log above)."
        }
        Start-Sleep -Seconds 3
    }
    docker compose exec -T web python -m app.cli seed-demo
    Write-Addresses
    if (-not $NoOpen) { Start-Process "$Web/g/demo" }
}

switch ($Command) {
    'up' { Invoke-Up }
    'status' { Test-Docker; docker compose --profile dev ps; Write-Addresses }
    'logs' { Test-Docker; docker compose --profile dev logs -f --tail 100 }
    'down' { Test-Docker; docker compose --profile dev down; Write-Host 'Stopped. Your data is kept; scripts\try.ps1 starts it again.' }
    'reset' { Test-Docker; docker compose --profile dev down --volumes; Write-Host 'Stopped and deleted the data. scripts\try.ps1 starts a clean installation.' }
}
