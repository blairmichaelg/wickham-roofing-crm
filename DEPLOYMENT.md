# DEPLOYMENT & OPERATIONAL RUNBOOK: V4 "WICKHAM ROOFING CRM"

This deployment playbook provides authoritative instructions for staging, launching, and maintaining the **Wickham Roofing V4 "Wickham Roofing CRM"** AI application stack. 

Engineered primarily for resilient local execution on Windows hardware within field branch offices, the pipeline can be spun up natively with single-click PowerShell automation or deployed to containerized cloud infrastructures (e.g., Render, Docker) for hybrid architectures.

---

## Part 1: Local Windows Field Office Deployment (Primary Mode)

The "Wickham Roofing CRM" architecture natively converts an office Windows PC or laptop into a high-concurrency CRM server, bridging public mobile field traffic via secure Edge Tunnels (Cloudflare) without requiring cloud virtual machine rentals.

### 1. Hardware & Software Requirements
- **Operating System**: Windows 10/11 Pro or Windows Server (Mac/Linux architectures supported via terminal equivalents).
- **Runtime Environment**: Python 3.11 or later installed and configured on PATH.
- **Git client**: Installed for pulling repository updates and synchronization.
- **Optional Cache Layer**: Docker Desktop or Windows Subsystem for Linux (WSL) if utilizing local Redis instance emulation (the automated boot scripts will orchestrate Redis automatically if found).

### 2. Workspace Initialization
1. Open PowerShell and clone the official project repository:
   ```powershell
   git clone https://github.com/blairmichaelg/wickham-roofing-crm.git
   cd wickham-roofing-crm
   ```
2. Provision a dedicated isolated Python virtual environment:
   ```powershell
   python -m venv venv
   .\venv\Scripts\activate
   ```
3. Install strict runtime application packages:
   ```powershell
   pip install --upgrade pip
   pip install -r requirements.txt
   ```

### 3. Secrets & Configuration (`.env`)
Create your functional environment configuration from the provided enterprise template:
```powershell
cp .env.example .env
```
Open `.env` in a secure editor and configure your production parameter tokens:

| Parameter Variable | Required Production Value | Description |
| :--- | :--- | :--- |
| `APP_ENV` | `production` | Isolates operational databases from test environments; activates WAL backup rules. |
| `LOG_LEVEL` | `INFO` | Controls structural console verbosity (set to `DEBUG` during active diagnostics). |
| `GEMINI_API_KEY` | `AIzaSy...` | Valid Google AI Studio Gemini API Key for vision and narrative processing. |
| `WEBHOOK_SECRET` | `32-char hex string` | Cryptographic secret for signing external notification endpoints. |
| `REDIS_URL` | `redis://127.0.0.1:6379/0` | Connection locator for local or networked Redis task queue broker. |


> [!CAUTION]
> Never commit `.env` or local `.db` files to Git version control. Ensure `.gitignore` guidelines remain intact when executing remote code synchronization.

### 4. Automated Windows Launch (Task Scheduler)
To eliminate manual setup errors in field branch environments, V4 includes automated PowerShell orchestration utilities invoked on system boot:

1. **First-Time Network Provisioning**: Run `scripts\dev\setup_network.ps1` once to automatically fetch and place the secure Cloudflare Web Tunneling binary into the isolated `tools/` directory.
2. **Master Production Boot Sequence**: The server is now configured to boot automatically via **Windows Task Scheduler**. Upon user login, Task Scheduler triggers the individual wrapper scripts located in `scripts\services\`:
   - `srv_redis.ps1`: Ensures Redis is running (or boots a container if needed).
   - `srv_fastapi.ps1`: Launches the Uvicorn FastAPI Server (Port 8000) with logging to `logs\fastapi_*.log`.
   - `srv_worker.ps1`: Launches the ARQ Asynchronous Queue Worker with logging to `logs\arq_worker_*.log`.
   - `srv_tunnel.ps1`: Spawns the Cloudflare Tunnel (`tools\cloudflared.exe`) and logs the public URL.
   
These wrapper scripts handle automated restart loops, port conflict resolution, and logging without manual intervention. To check the system health, verify the logs in the `logs\` directory or ping the `/health` endpoint.

---

## Part 2: Zero-Code Offsite Disaster Recovery (Google Drive)

To ensure enterprise data continuity without writing custom third-party cloud SDK wrappers (e.g., AWS S3 `boto3` calls), V4 integrates a "Zero-Code" background backup architecture utilizing Google Drive for Desktop:

1. **Install Google Drive for Desktop** on the Windows machine operating as the local Wickham Roofing CRM.
2. **Configure Folder Sync**: In Google Drive settings, map the local folder path `wickham_crm\data\backups` for continuous automatic synchronization to your secure business cloud drive.
3. **Automated Snapshot Engine**: The application's internal cron jobs continuously execute non-blocking SQLite `VACUUM INTO` operations, writing consistency-verified database snapshots directly into `data\backups\`.
4. **Automatic Cloud Preservation**: Google Drive silently monitors the target folder and automatically syncs all new snapshots to the cloud in real time. The internal cleanup engine automatically unlinks historical backups beyond a 10-file ceiling, maintaining optimal local disk footprint while preserving cloud disaster recovery capability.

---

## Part 3: Cloud Container Deployment (Optional Fallback Mode)

For hybrid deployments requiring hosting on remote cloud infrastructure, the repository natively includes container blueprints tuned for PaaS platforms (e.g., Render, Heroku, AWS ECS):

### 1. Render Infrastructure Blueprint
The included `render.yaml` configuration file automatically defines and launches a fully orchestrated cloud cluster:
1. Log into your [Render Cloud Dashboard](https://render.com) and navigate to **New + $\rightarrow$ Blueprint**.
2. Connect your private GitHub repository (`wickham-roofing-crm`).
3. Render reads `render.yaml` automatically, provisioning:
   - **Web Service (`wickham-ai-controller`)**: Using the root `Dockerfile` and `entrypoint.sh` script to run both Uvicorn and ARQ worker processes within a unified high-efficiency container.
   - **Key-Value Service (`wickham-redis`)**: An isolated internal Redis cache cluster accessible strictly over private container networks.
4. In the service dashboard environment tab, inject your required secrets (`GEMINI_API_KEY`, `WEBHOOK_SECRET`, `APP_ENV=production`) and deploy!

---

## Part 4: System Verification & Post-Deploy Health Checks

Whenever deploying a new build or performing maintenance on an active server, execute the verification baseline to verify zero regressions:

```powershell
# Verify complete automated test suite (must report 100% pass rate across 282 tests)
.\venv\Scripts\python.exe -m pytest tests/ -v

# Run Python static type verification
.\venv\Scripts\python.exe -m mypy app/

# Perform static analysis inspection
.\venv\Scripts\python.exe -m ruff check app/

# Verify Gemini AI integration end-to-end
.\venv\Scripts\python.exe scripts/manual_verify_photo_analysis.py
```

If all tests and linter checks pass cleanly, your local or cloud CRM deployment is completely hardened, mathematically proven, and operational for real-world production.
