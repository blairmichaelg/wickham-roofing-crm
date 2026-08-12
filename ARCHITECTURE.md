# SYSTEM ARCHITECTURE & TECHNICAL DESIGN: V4 "WICKHAM ROOFING CRM"

## 1. Executive Technical Summary

The **Wickham Roofing AI Controller (V4 "Wickham Roofing CRM")** is an advanced local-first CRM, financial ledger, and automated document synthesis engine. Developed to operate autonomously on field office laptops without requiring active cloud CRM subscriptions, V4 combines **SQLite in Write-Ahead Logging (WAL) mode**, **FastAPI**, **ARQ background workers over Redis**, and **Google Gemini 2.5 Flash** multimodal intelligence.

This document outlines the software structural patterns, separation of concerns, defensive security boundaries, and asynchronous orchestrations governing the repository.

---

## 2. High-Level System Architecture

```
   ┌─────────────────────────────────────────────────────────────┐
   │                 PUBLIC ACCESS LAYER (TLS)                   │
   │           Cloudflare Web Tunnel / Ngrok Edge               │
   └──────────────────────────────┬──────────────────────────────┘
                                  │
                                  ▼
   ┌─────────────────────────────────────────────────────────────┐
   │             FASTAPI ASYNCHRONOUS ENGINE (PORT 8000)         │
   │  ┌───────────────────────┐       ┌───────────────────────┐  │
   │  │   JWT Auth & RBAC     │       │ Sliding Window Rate   │  │
   │  │   Middleware Engine   │       │ Limiting Protection   │  │
   │  └───────────┬───────────┘       └───────────┬───────────┘  │
   └──────────────┼───────────────────────────────┼──────────────┘
                  │                               │
                  ▼                               ▼
   ┌──────────────────────────────┐   ┌──────────────────────────┐
   │  SQLITE WAL STATE MACHINE    │   │ REDIS BROKER (PORT 6379) │
   │  BEGIN IMMEDIATE Concurrency │   │ ARQ Worker Task Queue    │
   │  Role-Tailored SQL Views     │   └───────────┬───────────┘
   └──────────────┬───────────────┘               │
                  │                               ▼
                  │                   ┌──────────────────────────┐
                  │                   │  BACKGROUND WORKERS      │
                  │                   │  - Document Extractor    │
                  │                   │  - SupplementEngine      │
                  │                   │  - ReportLab Vault Builder│
                  │                   └───────────┬───────────┘
                  ▼                               ▼
   ┌─────────────────────────────────────────────────────────────┐
   │               LOCAL STORAGE & AUTOMATED VAULT               │
   │     data/ (Database & Backups)  |  field_docs/ (PDF Vault)  │
   └─────────────────────────────────────────────────────────────┘
```

---

## 3. Core Architectural Patterns

### A. Strict Bifurcation: Math Determinism vs. AI Intelligence
Because insurance supplements and material purchasing orders represent real financial liabilities, the pipeline strictly decouples mathematical reckoning from neural network evaluations:

1. **`SupplementEngine` (Pure Deterministic Kernel — `app/services/supplement_engine.py`)**:
   - Executes mathematically proven logic using physical roofing inputs (e.g., eave lengths, ridge lengths, valleys, square footage).
   - Dynamically calculates complexity-graded waste factors and evaluates regional climate rules (such as Ice & Water Shield mandates in specific municipalities) without database or network dependency.
   - Guaranteed deterministic execution: identical inputs always yield identical material calculations.

2. **`SupplementProcessor` (Worker Orchestrator — `app/workers/supplement_processor.py`)**:
   - Executes inside background ARQ Redis workers.
   - Manages orchestration: pulling PDF bytes from disk, running structural PDF extractions via `pdfplumber`, generating pure math discrepancy reports via `SupplementEngine`, invoking Gemini 2.5 Flash exclusively for natural language narrative crafting, and rendering legal ReportLab PDFs.

### B. The Universal Claim AST
To eliminate semantic processing errors and prevent bad adjuster data from corrupting calculations, incoming Statements of Loss (SoL) and EagleView reports pass through a multi-tier ingestion barrier defined in `app/core/ingestion_models.py`:
- Enforces Pydantic V2 architectural verification on every line item.
- Proves carrier financial math (Gross Replacement Cost Value vs. Recoverable Depreciation vs. Actual Cash Value). If carrier math contains internal inconsistencies, ingestion halts immediately, sending the job into `PENDING_MANUAL_REVIEW`.
- Embeds SHA256 file hashing natively to guarantee cryptographic trace auditability and prevent duplicate processing.

### C. The Fail-Loud / Resume Lifecycle
When anomalies arise (e.g., zero-length eaves or unsupported carrier PDF formatting), the pipeline follows an intentional "Fail-Loud" recovery design:
1. **Interception & Flagging**: The ARQ worker traps the exception, inserts an audit record into the `supplement_flags` database table (`MANUAL REVIEW REQUIRED: <reason>`), and halts state progression at `PENDING_MANUAL_REVIEW`.
2. **Administrative Triage**: Using the technical control panel (`PATCH /api/field/jobs/{job_id}/flags/{flag_id}`), administrators correct erroneous readings directly in the UI, generating an immutable audit record (`RESOLVED: <note>`).
3. **Resumption Engine**: Calling `POST /api/field/jobs/{job_id}/resume-supplement` re-enqueues the job into ARQ with `resume=True`. The background task reconstitutes saved state from the `supplement_reports` SQL cache, bypasses raw network parsing, and resumes PDF synthesis effortlessly.

---

## 4. Security & Isolation Boundaries

### A. Cryptographic Authentication & RBAC
- **No Silent Zeros Command**: All user accounts and field representatives authenticate using 4-digit PINs stored securely using `bcrypt` adaptive hashing.
- **Symmetric Token Pinging**: System tokens rely exclusively on signed JSON Web Tokens utilizing the `HS256` symmetric signing algorithm. `None` algorithms or unsigned headers are rejected at the edge.
- **Role Isolation Matrix**: API endpoints depend strictly on architectural decorators (`verify_admin`, `verify_accounting`, `verify_operations`, `verify_field`). Background ARQ workers reject any enqueued payload lacking an authenticated execution context role.

### B. Defense-in-Depth Protection Layers
- **Sliding-Window Rate Limiting**: Heavy asynchronous endpoints (`/material_order`, `/supplement_docs/upload`, `/resume-supplement`) pass through an in-memory sliding window limiter (capped at 3 requests per 10-second window per IP) to guard against queue starvation.
- **Path Traversal Shield**: Document rendering and file retrieval routes utilize rigorous filename sanitization (`sanitize_download_filename` and strict `uuid.UUID()` parameter binding) to eliminate Relative Path Inclusion (`../`) vulnerabilities.
- **IDOR Protection**: Field endpoints enforce ownership queries via compound constraints (`WHERE id = ? AND canvasser_rep_id = ?`), preventing cross-canvasser data enumeration.

---

## 5. Storage Engine & Resilience

### A. SQLite 3 WAL & Immediate Concurrency
Running multi-role web servers over standard SQLite files historically risked database locked errors (`SQLITE_BUSY`). V4 resolves this via:
- **WAL Mode Enabling**: Write-Ahead Logging allows simultaneous non-blocking reads across the active operations, accounting, and field dashboards during active background writes.
- **Explicit `BEGIN IMMEDIATE` Transactions**: Database mutations across state transitions and QBO accounting batch updates are explicitly bound within atomic `BEGIN IMMEDIATE` transaction closures, preventing read-to-write TOCTOU race conditions.
- **Role-Tailored SQL Views**: Specialized native database views (`live_material_board` and `financial_delta_view`) pre-aggregate complex ledger computations in SQL C-code for zero-latency dashboard delivery.

### B. Automated Hot Snapshots & Disaster Recovery
- **Hot Snapshots**: The internal scheduling worker periodically executes non-locking SQLite `VACUUM INTO` operations to generate consistency-verified point-in-time database backups inside `data/backups/`.
- **Anti-Bloat Cleanup Engine**: To prevent disk space starvation on office laptops, the backup routine automatically unlinks historical database archives beyond a strict 10-snapshot maximum threshold.

---

## 6. Authoritative Repository Directory Tree

```
wickham-roofing-crm/
├── app/                        # Application Source Code Kernel
│   ├── api/                    # FastAPI Routers (office_routes, field_routes, auth)
│   ├── core/                   # SQLite WAL Database, Schema Migrations & Ingestion Models
│   ├── services/               # Deterministic SupplementEngine, AI Parser & PDF Generators
│   ├── workers/                # ARQ Asynchronous Queue Consumers & Background Settings
│   └── templates/              # Tailored Jinja2 Reactive SPA View Templates
├── docs/                       # Authoritative Operational & Role Instruction Manuals
│   ├── accounting_guide.md     # Ledger, check payments, and QBO CSV export procedures
│   ├── admin_tech_guide.md     # Admin controls, triage procedures, and rep onboarding
│   ├── canvasser_field_guide.md# Mobile offline-first SPA operational manual
│   ├── field_runbook.md        # Emergency operational diagnostics and incident mitigation
│   ├── operations_guide.md     # Material orders, scheduled installations, and site flags
│   └── security_tasks.md       # Technical audit specifications and security authorization limits
├── tests/                      # 282+ Fully Asserted Integration & Unit Test Scripts
├── tools/                      # Networking & Tunneling Tools (cloudflared.exe binary isolate)
├── building_codes/             # Zero-Cost Local RAG Municipal Building Code Archives
├── data/                       # Local Storage Repository (SQLite Main DB & Hot WAL Backups)
├── field_docs/                 # Vaulted Static Output Artifacts (Generated PDFs)
├── field_photos/               # Ingested High-Resolution Roof Inspection Photos
├── sample_pdfs/                # Calibration & Regression Test Sample Documents
├── signed_agreements/          # Vaulted Client Contingency & Contract Signatures
├── generated_exports/          # Cached Accounting QuickBooks Online (QBO) CSV Exports
├── scripts/                    # Maintenance and operational scripts
│   ├── dev/
│   │   ├── setup_network.ps1   # Cloudflare Tunnel automated download utility
│   │   └── start_dev.ps1       # Local developer boot sequence script (Port 8001)
│   └── services/               # Wrapper scripts for Task Scheduler (srv_*.ps1)
├── render.yaml                 # Infrastructure-as-Code container deployment specifications
├── Dockerfile                  # Container build instructions for cloud fallback hosting
├── Procfile                    # Buildpack process directives for cloud environments
├── pyproject.toml              # Tooling configuration (Ruff linter, Mypy typings, Pytest)
└── requirements.txt            # Explicit Python package dependency bindings
```

---

## 7. Role → Route → Service → Domain → Persistence Layer Diagram

The diagram below maps how requests from authenticated personas flow through routing, orchestration, core domain logic, and down to persistence.

```
┌────────────────────────────────────────────────────────────────────────┐
│                        ROLE / ACCESS LAYER                             │
│  [Field Canvasser]    [Operations Mgr]    [Accounting]     [Admin Rep] │
└─────────┬───────────────────┬──────────────────┬───────────────┬───────┘
          │ (REST/WS)         │ (REST/WS)        │ (REST/WS)     │ (REST)
          ▼                   ▼                  ▼               ▼
┌────────────────────────────────────────────────────────────────────────┐
│                       ROUTING LAYER (FastAPI)                          │
│                                                                        │
│                      app/main.py (Re-export Shim)                      │
│                                   │                                    │
│                     app/server.py (App Factory)                        │
│                                   │                                    │
│  ┌───────────────────────┬────────┴──────────────┬──────────────────┐  │
│  │ app/api/field_routes  │ app/api/office_routes │ app/api/auth     │  │
│  │ app/api/operations    │ app/api/admin_jobs    │ app/api/webhooks │  │
│  └───────────┬───────────┴───────────┬───────────┴────────┬─────────┘  │
└──────────────┼───────────────────────┼────────────────────┼────────────┘
               │                       │                    │
               ▼                       ▼                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                     SERVICE / ORCHESTRATION LAYER                      │
│                                                                        │
│  ┌────────────────────────┐  ┌─────────────────────────┐  ┌─────────┐  │
│  │ app/services/ai       │  │ app/services/pdf/       │  │ app/    │  │
│  │ (Gemini File API/CoT)  │  │ (ReportLab Documents)   │  │ workers/│  │
│  ├────────────────────────┤  ├─────────────────────────┤  │ (ARQ    │  │
│  │ app/services/qbo_export│  │ app/services/document_  │  │ Task    │  │
│  │ (QuickBooks Online)    │  │ parser (pdfplumber)     │  │ Queue)  │  │
│  └───────────┬────────────┘  └───────────┬─────────────┘  └────┬────┘  │
└──────────────┼───────────────────────────┼─────────────────────┼───────┘
               │                           │                     │
               ▼                           ▼                     ▼
┌────────────────────────────────────────────────────────────────────────┐
│                       DOMAIN / CORE LOGIC KERNEL                       │
│                                                                        │
│  ┌───────────────────────────────┐   ┌──────────────────────────────┐  │
│  │ app/services/supplement_engine│   │ app/core/code_router         │  │
│  │ (Pure Deterministic Math)     │   │ (Zero-Cost Local RAG)        │  │
│  ├───────────────────────────────┤   ├──────────────────────────────┤  │
│  │ app/core/job_costing          │   │ app/core/complexity          │  │
│  │ (Margin & Commissions math)   │   │ (Complexity Rating formulas) │  │
│  └───────────┬───────────────────┘   └──────────────┬───────────────┘  │
└──────────────┼──────────────────────────────────────┼──────────────────┘
               │                                      │
               ▼                                      ▼
┌────────────────────────────────────────────────────────────────────────┐
│                       PERSISTENCE & STATE LAYER                        │
│                                                                        │
│  ┌────────────────────────┐  ┌─────────────────────────┐  ┌─────────┐  │
│  │   SQLite Database      │  │      Redis DB Cache     │  │ Local   │  │
│  │   (data/wickham.db)    │  │     (arq Job Queue)     │  │ Vault   │  │
│  │   - Write-Ahead Log    │  │     - Session tokens    │  │ (PDFs/  │  │
│  │   - BEGIN IMMEDIATE    │  │     - Task coordination │  │ photos) │  │
│  └────────────────────────┘  └─────────────────────────┘  └─────────┘  │
└────────────────────────────────────────────────────────────────────────┘
```