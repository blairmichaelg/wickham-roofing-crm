# =====================================================================
# V4 Truck Server - Local Boot Sequence
# =====================================================================
# This script provides a one-click developer experience by:
# 1. Intelligently booting Redis (via Docker or WSL) if offline.
# 2. Executing the Python pre-flight diagnostics checker.
# 3. Spawning two independent terminal windows for FastAPI and ARQ.
# =====================================================================

Write-Host "V4 Truck Server - One-Click Boot Sequence Initiated" -ForegroundColor Cyan
Write-Host "=========================================================" -ForegroundColor Cyan

# ---------------------------------------------------------------------
# Phase 1: Automated Redis Boot Sequence
# ---------------------------------------------------------------------
Write-Host "[1/4] Checking local Redis broker (Port 6379)..." -ForegroundColor Yellow

$portOpen = $false
try {
    $tcp = New-Object System.Net.Sockets.TcpClient
    $result = $tcp.BeginConnect("127.0.0.1", 6379, $null, $null)
    $success = $result.AsyncWaitHandle.WaitOne([TimeSpan]::FromSeconds(1))
    if ($success) {
        $portOpen = $tcp.Connected
    }
} catch {
    $portOpen = $false
} finally {
    if ($tcp) { $tcp.Close() }
}

if ($portOpen) {
    Write-Host "[SUCCESS] Port 6379 is active. Redis is already running." -ForegroundColor Green
} else {
    Write-Host "[INFO] Redis is offline. Attempting automated startup sequence..." -ForegroundColor Yellow
    
    # Preference 1: Docker
    docker info >$null 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "[INFO] Docker daemon detected. Orchestrating container..." -ForegroundColor Magenta
        
        $container = docker ps -a -q -f name=v4-redis-server
        if ($container) {
            Write-Host "[INFO] Starting existing 'v4-redis-server' container..." -ForegroundColor Magenta
            docker start v4-redis-server >$null
        } else {
            Write-Host "[INFO] Provisioning new 'v4-redis-server' container..." -ForegroundColor Magenta
            docker run -d -p 6379:6379 --name v4-redis-server redis >$null
        }
    } 
    # Fallback 1: WSL
    else {
        Write-Host "[INFO] Docker unavailable. Falling back to WSL..." -ForegroundColor Magenta
        wsl -l -v >$null 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Host "[INFO] WSL detected. Booting redis-server daemon as root..." -ForegroundColor Magenta
            wsl -u root -- /usr/bin/redis-server --bind 0.0.0.0 --daemonize yes >$null
        } else {
            Write-Host "[ERROR] Neither Docker nor WSL is available. Cannot auto-boot Redis!" -ForegroundColor Red
            exit 1
        }
    }
    
    Write-Host "[INFO] Providing 3-second initialization buffer..." -ForegroundColor Yellow
    Start-Sleep -Seconds 3
}

# ---------------------------------------------------------------------
# Phase 2: Virtual Environment Check
# ---------------------------------------------------------------------
Write-Host "`n[2/4] Validating Virtual Environment..." -ForegroundColor Yellow
if (-Not (Test-Path ".\venv\Scripts\activate.ps1")) {
    Write-Host "[ERROR] Virtual environment not found at .\venv\" -ForegroundColor Red
    Write-Host "Please run 'python -m venv venv' and install requirements first."
    exit 1
}

# ---------------------------------------------------------------------
# Phase 3: Pre-Flight Diagnostics
# ---------------------------------------------------------------------
Write-Host "`n[3/4] Executing Python Pre-Flight Diagnostics..." -ForegroundColor Yellow
.\venv\Scripts\python.exe -c "import app.main; import app.config; print('Pre-flight check passed: app.main & app.config verified.')"

if ($LASTEXITCODE -ne 0) {
    Write-Host "`n[ABORT] Pre-flight checks failed. Halting boot sequence to prevent worker crashes." -ForegroundColor Red
    exit $LASTEXITCODE
}

# ---------------------------------------------------------------------
# Phase 4: Application & Worker Launch
# ---------------------------------------------------------------------
Write-Host "`n[4/4] Spawning persistent terminal windows..." -ForegroundColor Yellow

Write-Host "-> Booting FastAPI Server (Port 8001)..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList "-NoExit", "-Command", ".\venv\Scripts\activate; `$env:APP_ENV='dev'; python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8001"

Write-Host "-> Booting ARQ Background Worker (Dev)..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList "-NoExit", "-Command", ".\venv\Scripts\activate; `$env:APP_ENV='dev'; python -m arq app.workers.settings.WorkerSettings"

Write-Host "`n=========================================================" -ForegroundColor Green
Write-Host "SUCCESS: The One-Click CRM stack is fully operational!" -ForegroundColor Green
Write-Host "Two separate terminal windows have been spawned." -ForegroundColor Green
Write-Host "=========================================================" -ForegroundColor Green
