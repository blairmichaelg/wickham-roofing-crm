# Wickham Roofing CRM — V4

[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.109%2B-00a393.svg)](https://fastapi.tiangolo.com/)
[![SQLite WAL](https://img.shields.io/badge/Database-SQLite%20WAL-003B57.svg)](https://www.sqlite.org/wal.html)
[![AI Engine: Gemini 2.5 Flash](https://img.shields.io/badge/AI%20Engine-Gemini%202.5%20Flash-8A2BE2.svg)](https://deepmind.google/technologies/gemini/)
[![Tests: 446 Passing (100%)](https://img.shields.io/badge/Tests-446%20Passed%20(100%25)-brightgreen.svg)](https://pytest.org/)
[![Version](https://img.shields.io/badge/Version-2.5.4-orange.svg)](https://github.com/blairmichaelg/wickham-roofing-crm)

The **Wickham Roofing CRM (V4)** is a proprietary, local-first operational platform designed to automate insurance roofing production from field lead intake to financial ledger reconciliation.

Engineered to operate entirely offline or via zero-cloud tunneling directly from field office hardware, V4 completely eliminates third-party SaaS dependency by orchestrating deterministic insurance math, forensic AI roof analysis, automated paperwork matrices, and QuickBooks Online (QBO) invoice exporting over a self-healing SQLite state machine.

---

## 🏛️ System Architecture & Operational Workflows

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         WICKHAM ROOFING CRM V4                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  FIELD OPERATIONS (Mobile SPA via Cloudflare/Ngrok Tunnel)                  │
│  ┌──────────────┐      ┌──────────────┐      ┌───────────────────────────┐  │
│  │ Lead Intake  │ ───► │ Offline-First│ ───► │ Cryptographically Hashed    │  │
│  │ & Signatures │      │ IndexedDB    │      │ EagleView & Photo Uploads │  │
│  └──────────────┘      └──────────────┘      └───────────────────────────┘  │
│          │                                                  │               │
│          ▼                                                  ▼               │
│  OFFICE & TECHNICAL CONTROL PANEL (Local Uvicorn Dashboard)                 │
│  ┌──────────────┐      ┌──────────────┐      ┌───────────────────────────┐  │
│  │ Real-Time    │ ───► │ Deterministic│ ───► │ Automated Climate Gating  │  │
│  │ Margin Gate  │      │ SoL / EV Math│      │ & Manual Review Overrides │  │
│  └──────────────┘      └──────────────┘      └───────────────────────────┘  │
│          │                                                  │               │
│          ▼                                                  ▼               │
│  THE PAPERWORK & ACCOUNTING MATRIX (ReportLab & QBO Export Engine)          │
│  ┌──────────────┐      ┌──────────────┐      ┌─────────────┐  ┌──────────┐  │
│  │ Supplier POs │      │ Statutory GA │      │ QBO Invoice │  │ 1099 Rep │  │
│  │ & Estimates  │      │ Compliance   │      │ CSV Export  │  │ Ledger   │  │
│  └──────────────┘      └──────────────┘      └─────────────┘  └──────────┘  │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 🔐 Role-Based Access Control (RBAC) & The Four Pillars

The system isolates operational data across four distinct user roles, authenticated via secure, database-hashed (`bcrypt`) 4-digit PINs and signed with symmetric algorithm-confineable (`HS256`) JSON Web Tokens (JWTs):

1. **Field Canvassers (`verify_field`)**: Mobile SPA interface scoped via strict IDOR boundaries (`assert_field_rep_owns_job`). Allows canvassers to input leads, collect HTML5 canvas client signatures offline, upload EagleView reports, and view only their personal commission statements.
2. **Operations & Production (`verify_operations`)**: A streamlined, read-only departure board allowing production managers to oversee installation schedules, material orders (`MaterialBOM`), and toggle site material flags (`materials_ordered`, `materials_on_site`).
3. **Accounting Ledger (`verify_accounting`)**: Full financial oversight across all jobs. Empowers financial staff to record Actual Cash Value (ACV) and Supplement check payments, adjust manual commission percentage overrides per job, and perform atomic QuickBooks Online (QBO) CSV invoice exports.
4. **Admin Technical Control Panel (`verify_admin`)**: Comprehensive triage and pipeline oversight. Enables administrators to review and resolve AI calculation flags, manage field rep onboarding/identities, trigger emergency state machine bypasses, and monitor background queue telemetry.

---

## ⚡ Core Technologies & Engineering Pillars

| Component | Technology / Architecture | Why It Was Chosen |
| :--- | :--- | :--- |
| **Backend Framework** | Python 3.11+ / FastAPI / Uvicorn | Extreme asynchronous performance, built-in Open API schemas, and native Pydantic V2 typing. |
| **Database Engine** | SQLite 3 in WAL Mode | Zero-latency local operations, instantaneous file locks with `BEGIN IMMEDIATE`, and total immunity to SaaS server dropouts. |
| **AI Processing** | Google Gemini 2.5 Flash | Advanced multimodal capabilities for forensic roof image analysis and automated insurance discrepancy narratives. |
| **Background Task Queue** | ARQ over Local Redis | Asynchronous background document parsing, PDF compilation, and building code RAG lookups without halting UI threads. |
| **Document Vault Engine** | ReportLab + pdfplumber | Deterministic, precision-aligned generation of statutory legal notices, supplier POs, and evidence grids. |
| **Frontend & UI** | Vanilla JS + Tailwind CSS | Zero-bundle bloat, offline Service Worker capabilities with IndexedDB persistence, and crisp reactive dashboards. |
| **Quality & Assurance** | Pytest / Mypy / Ruff | **351 tests** passing at a 100% pass rate; 75.27% code coverage enforced by CI gate; strict static analysis zero-error compliance across all layers. |

---

## 🛠️ Key Engine Features

### 1. Deterministic Math & Anti-Hallucination Claim AST

To ensure real-world financial accuracy, the pipeline strictly separates mathematical calculations from artificial intelligence reasoning.

- **`SupplementEngine`**: An isolated pure-Python mathematical kernel that reconciles EagleView physical dimensions against carrier Statements of Loss (SoL) without AI guesswork.
- **Universal Claim AST**: Pydantic V2 schemas that mathematically prove carrier RCV/ACV totals and enforce strict SHA256 provenance tracking before triggering document synthesis.
- **Climate Zone Gating**: Automates statutory building code enforcement (e.g., Ice & Water Shield rules in northern climates vs. Georgia municipal codes) using zero-cost RAG databases.

### 2. Resilient Infrastructure & Security

- **In-Memory Sliding Window Rate Limiter**: Protects computationally expensive ARQ job enqueuing endpoints against accidental or malicious queue flooding.
- **Self-Healing Realtime WebSockets**: Automated frontend reconnect logic coupled with server-side active background heartbeat loops instantly sweeps dead client sockets.
- **Path Traversal & IDOR Defense**: All document download endpoints apply rigorous cryptographic hashing and filename sanitization (`sanitize_download_filename`).

### 3. Non-Blocking "Naked Lead" Sales Workflow

Field reps can now capture minimal lead data (name, address, phone) at the door **without requiring an immediate signature or photos**. The system stores the lead as `LEAD_CAPTURED`, keeping it visible to the core team without clogging the active production pipeline.

- **Resume & Sign**: From *My Recent Jobs*, reps tap **✍️ Resume & Sign** to re-open the intake form pre-populated with saved data and walk through the full contingency agreement for on-the-spot or follow-up signing.
- **Unsigned Agreement PDF**: Reps can download or email the homeowner a printable unsigned contingency agreement directly from the job card — no office visit required.
- **Evidence Grid Pitch Tool**: The Inspection Evidence Grid PDF (storm findings, hail impacts, weather data) is accessible directly from the job card, enabling reps to present objective storm damage proof before asking for the signature.

### 4. Real-Time Storm Activity Monitor Ingestion & Alerting

The CRM automatically monitors and ingests Local Storm Reports (LSR) directly from the National Weather Service (NWS) ArcGIS server to identify hail, wind, and tornado occurrences.

- **Materiality Filter**: Excludes minor, zero-magnitude weather occurrences to ensure sales reps are only seeing actionable, damage-prone storm events.
- **WebSocket Broadcasts**: Pushes immediate severe weather alerts directly to active field and office dashboards when a storm falls within the office service boundary.
- **Canvassing Opportunities**: Aggregates historic regional storm activity directly inside the canvassing control panel, enabling operators to target door-knocking efforts.

---

## 🚀 Quick Start Guide

### Hardware Prerequisites

- Windows, macOS, or Linux machine with Python 3.11+ and Git installed.
- Redis broker (optional for local simulation; automated via Docker/WSL in boot scripts).

### 1. Repository Setup & Virtual Environment

```powershell
# Clone repository
git clone https://github.com/blairmichaelg/wickham-roofing-crm.git
cd wickham-roofing-crm

# Provision virtual environment
python -m venv venv
.\venv\Scripts\activate          # Windows PowerShell
# source venv/bin/activate         # Linux / Mac Bash

# Install strict dependency requirements
pip install -r requirements.txt
```

### 2. Environment Configuration

Copy the supplied template and inject your Gemini API credentials:

```powershell
cp .env.example .env
# Open .env and enter your valid GEMINI_API_KEY
```

### 3. Verification & Execution

Validate system stability against the 312-test verification matrix before firing the application engines:

```powershell
# Execute comprehensive automated test matrix
python -m pytest tests/ -v

# Run local development server (Port 8000)
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

### 4. Fresh Demo Reset (Recommended Before Live Demos)

If you need the CRM in a completely clean, empty-job state for a presentation, run the built-in reset script before starting the stack:

```powershell
python scripts/db_demo_reset.py
```

This clears all jobs, resets the demo database to a pristine state, removes uploaded artifacts from the document directories, and restores the default demo field rep `Jerry Grubb` with PIN `1111`.

> [!TIP]
> **Windows One-Click Automation**: On Windows desktop environments, the server is now configured to boot automatically via **Task Scheduler** on login. Task Scheduler invokes a suite of wrapper scripts (`scripts\services\srv_redis.ps1`, `srv_fastapi.ps1`, `srv_worker.ps1`, `srv_tunnel.ps1`) that handle automated recovery, logging, and port binding.
>
> **Service Health & Restart**: Log files for all background services are located in the `logs\` directory. To manually restart a service, you can terminate its window and re-run the respective `srv_*.ps1` script from the `scripts\services\` directory, or simply use `Restart-Computer` for a clean reboot and auto-recovery. Health can be checked via the `/health` endpoint.

---

## 📚 Documentation & Guides

Comprehensive operator manuals and operational runbooks are maintained directly within the `docs/` folder:

- **[Accounting Guide](docs/accounting_guide.md)**: Ledger instruction manual for check tracking, commission adjustments, and QBO invoice exporting.
- **[Operations Guide](docs/operations_guide.md)**: Schedule and material flag instructions for production dispatchers.
- **[Canvasser Field Guide](docs/canvasser_field_guide.md)**: Mobile SPA training manual for field personnel.
- **[Admin & Technical Guide](docs/admin_tech_guide.md)**: Master triage, rep administration, and emergency override procedures.
- **[Field Runbook](docs/field_runbook.md)**: Emergency troubleshooting playbook for connectivity or state machine issues during live deployment.
- **[Security Architecture](docs/security_tasks.md)**: Authorization boundaries and Phase 3 cryptographic security specifications.
- **[Testing Guide](docs/testing.md)**: Test coverage map, thresholds, and how to run the full matrix with coverage reporting.

---

## ⚖️ License & Proprietary Notice

**Proprietary Software** — Copyright © 2026 Wickham Roofing LLC.  
All rights reserved. Unauthorized reproduction, adaptation, distribution, or decompilation of this software, or any portion thereof, is strictly prohibited without explicit authorization from Wickham Roofing LLC.
