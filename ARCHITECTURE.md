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

### D. Storm Ingestion & Spatial Alerting Engine
To provide door-knocking sales reps with real-time, zero-cost weather reports near territory coordinates, the system features a live NWS ingestion service:
1. **NOAA MapServer Ingestion (`app/services/storm_feed.py`)**:
   - Queries NWS ArcGIS MapServer (72-hour Local Storm Reports Layer 2) using a coordinate bounding box centered around the primary corporate office.
   - Operates entirely client-side and server-side without external reverse-geocoding (Nominatim OpenStreetMap) or paid APIs, complying with public NWS usage guidelines.
2. **Idempotence & Unique Constraints**:
   - Uses SQLite `INSERT OR IGNORE` with a unique index constraint on `dedup_key` (constructed via event type, latitude/longitude coordinates rounded to 3 decimal places, and event timestamp).
3. **Real-time Alerting and WebSocket Propagation**:
   - The ARQ background worker (`app/workers/storm_worker.py`) polls NWS hourly. If a new report exceeds severity thresholds (hail >= 1.0 inch, wind >= 50 mph, or tornado), it publishes a storm event to Redis (`channel:storm_alerts`).
   - WebSockets client-side in the office admin dashboard and mobile field app receive instant pushes to render alert overlays, increment in-memory event tallies, and automatically trigger a background refresh of prioritized storm-target ZIPs.
4. **Unified Storm Radar & Canvassing Intelligence**:
   - Standardized `StormRadar` client module (`app/static/js/storm_radar.js`) shared between the Admin Dashboard and Field Mobile PWA.
   - All summaries enforce configured thresholds (`min_hail_inches`, `min_wind_mph`) and lookback windows (`storm_canvassing_window_hours`, `storm_fresh_window_hours`).
   - Both widgets render top canvassing target ZIPs ranked by deterministic storm severity scores, showing distinct hail/wind event counts, max magnitudes, and human-friendly relative event ages.
5. **Sales Enablement & Field Pipeline Acceleration**:
   - Job APIs automatically inject storm activity flags (`has_recent_hail`, `has_recent_wind`, `recent_hail_max_inches`, `recent_wind_max_mph`, `storm_window_hours`) derived from live SQLite weather data into job objects across field and admin boards.
   - Field app renders contextual badges in "My Recent Jobs" and surfaces "Next Best Action" hints (guiding reps to review storm evidence and request contingency signatures for storm-impacted leads).
   - Intake form calls `/api/field/storms/{zip}` on ZIP entry to present pre-computed, compliant sales pitch talking points.
   - Target ZIP cards feature one-click filtering for instant territory job isolation with active filter badges.

### E. API Architecture & Domain Decomposition (Office Router)
To avoid monolithic API files and enforce clean architectural domain boundaries, `app/api/office_routes.py` has been decomposed into dedicated domain submodules within `app/api/office/`, mounted under the unified `/api/office` prefix:
- **`app/api/office/billing.py`**: Handles job financial calculations, QBO export triggers, commission payouts, contractor invoice generation, and progress billing schedules.
- **`app/api/office/scheduling.py`**: Manages installation crew schedules, supplier material order synthesis, manual flashing requirements, operations brief generation, and storm target intelligence.
- **`app/api/office/contracts.py`**: Manages measurement report and Statement of Loss ingestion (EagleView/Hover), evidence grid rendering, supplement PDF pipelines, inspection letters, and document delivery vaults.
- **`app/api/office/jobs.py`**: Manages core job CRUD, triage resolution, shingle/claim info metadata patches, canvasser reassignments, pipeline summaries, and review/referral intake.
- **`app/api/office/router.py`**: Composite router registering all domain modules under `/api/office` (`office_ux` tag).
- **`app/api/office_routes.py`**: Re-export shim maintaining 100% backward compatibility for existing callers, test suites, and dynamic patch targets.

### F. API Architecture & Domain Decomposition (Field Router)
Similarly, `app/api/field_routes.py` has been decomposed into domain-focused submodules within `app/api/field/`, mounted under the unified `/api/field` prefix (with strict `verify_field` RBAC gate):
- **`app/api/field/leads.py`**: Field lead intake (`POST /jobs`), rep-scoped job listing (`GET /jobs`), job detail inspection, and status management.
- **`app/api/field/photos.py`**: Photo and voice note ingestion (`POST /jobs/{id}/photos`, `POST /jobs/{id}/voice`), orientation validation, and metadata extraction.
- **`app/api/field/signatures.py`**: Customer agreement and contract signature capture (`POST /jobs/{id}/sign-contingency`, `POST /jobs/{id}/sign-retail`), base64 decoding, format sanitization, and state transitions.
- **`app/api/field/documents.py`**: Field document retrieval, PDF agreement generation on the fly, and download vaults (`GET /jobs/{id}/documents/unsigned-contingency`, etc.).
- **`app/api/field/sales_tools.py`**: Real-time sales enablement, objection handling, pitch scripts, and Gemini-assisted pitch generation (`POST /door-pitch`, `POST /objection`).
- **`app/api/field/radar.py`**: Field storm radar, storm activity summaries, and localized target prioritization (`GET /storms/targets`, `GET /storms/{zipcode}`).
- **`app/api/field/router.py`**: Composite router registering all domain modules under `/api/field` with RBAC verification.
- **`app/api/field_routes.py`**: Re-export shim maintaining 100% backward compatibility for existing callers and test patch targets.

---

## 4. Security & Isolation Boundaries

### A. Cryptographic Authentication & RBAC
- **No Silent Zeros Command**: All user accounts and field representatives authenticate using 4-digit PINs stored securely using `bcrypt` adaptive hashing.
- **Symmetric Token Architecture**: System tokens rely exclusively on signed JSON Web Tokens utilizing the `HS256` symmetric signing algorithm with environment-provided secrets (`JWT_SECRET`). `None` algorithms, tampered signatures, or missing claims are rejected at the edge with HTTP 401.
- **Flexible Token Extraction**: Token dependencies inspect standard `Authorization: Bearer <token>` headers, legacy `x-internal-token` headers, or HttpOnly `auth_token` cookies.
- **Role Isolation Matrix**: API endpoints depend strictly on architectural decorators (`verify_admin`, `verify_accounting`, `verify_operations`, `verify_field`). Background ARQ workers reject any enqueued payload lacking an authenticated execution context role.

### B. Defense-in-Depth Protection Layers
- **Authentication Brute-Force Lockout**: `/auth/login` and PIN authentication routes enforce an IP-aware sliding window lockout (`app/services/rate_limit.py`). Failing 5 consecutive attempts within a 60-second window triggers an immediate HTTP 429 Too Many Requests lockout. Successful authentication immediately clears the failure counter.
- **Sliding-Window Rate Limiting**: Heavy asynchronous endpoints (`/material_order`, `/supplement_docs/upload`, `/resume-supplement`, `/statement-of-loss`) pass through an in-memory sliding window limiter (capped at 3 requests per 10-second window per IP) to guard against queue starvation.
- **Path Traversal Shield**: Document rendering and file retrieval routes utilize rigorous filename sanitization (`sanitize_download_filename` and strict `uuid.UUID()` parameter binding) to eliminate Relative Path Inclusion (`../`) vulnerabilities.
- **IDOR Protection**: Field endpoints enforce ownership queries via compound constraints (`WHERE id = ? AND canvasser_rep_id = ?`), preventing cross-canvasser data enumeration.

---

## 5. Storage Engine & Resilience

### A. SQLite 3 WAL & Immediate Concurrency
Running multi-role web servers over standard SQLite files historically risked database locked errors (`SQLITE_BUSY`). V4 resolves this via:
- **WAL Mode & Busy Timeout (30,000ms)**: Write-Ahead Logging allows simultaneous non-blocking reads across the active operations, accounting, and field dashboards during active background writes. All SQLite connections across FastAPI request lifecycles and background ARQ workers enforce `PRAGMA busy_timeout = 30000;` (30 seconds) and connection timeout of 30.0s to eliminate transient lock contention during heavy I/O bursts.
- **Explicit `BEGIN IMMEDIATE` Transactions**: Database mutations across state transitions and QBO accounting batch updates are explicitly bound within atomic `BEGIN IMMEDIATE` transaction closures, preventing read-to-write TOCTOU race conditions.
- **Automated WAL Checkpoints**: The ARQ worker lifecycle runs scheduled `PRAGMA wal_checkpoint(TRUNCATE);` sweeps (and post-heavy batch generation hooks) via `wal_checkpoint_truncate()` to keep WAL files compact, prevent write amplification, and release uncommitted page cache.
- **Role-Tailored SQL Views**: Specialized native database views (`live_material_board` and `financial_delta_view`) pre-aggregate complex ledger computations in SQL C-code for zero-latency dashboard delivery.


### B. Automated Hot Snapshots & Disaster Recovery
- **Hot Snapshots**: The internal scheduling worker periodically executes non-locking SQLite `VACUUM INTO` operations to generate consistency-verified point-in-time database backups inside `data/backups/`.
- **Anti-Bloat Cleanup Engine**: To prevent disk space starvation on office laptops, the backup routine automatically unlinks historical database archives beyond a strict 10-snapshot maximum threshold.

### C. Integer Cents Currency & Financial Precision
- **Zero-Float Accounting Guarantee**: All monetary values across `financials`, `jobs`, `pricing`, and progress billing ledgers are stored strictly as `INTEGER` cents (e.g., `revenue_cents`, `carrier_rcv_cents`, `default_rate_cents`). Legacy SQLite `REAL` currency columns are completely eliminated.
- **Pydantic Validation & Coercion**: Ingestion models and API payloads utilize `@field_validator(mode="before")` to cleanly parse currency strings (`$1,250.00`), numbers, and boundary amounts, rejecting negative values and computing integer cents deterministically.
- **Presentation & QBO Export Formatting**: User-facing templates, ReportLab PDF generators, and QuickBooks Online CSV exports format integer cents into exact two-decimal string representations (`f"{cents / 100.0:.2f}"`) at the presentation boundary without intermediate floating-point state drift.


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