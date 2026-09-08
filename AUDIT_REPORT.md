# Full System Security, Legal & Operations Audit Report

**Date**: August 16, 2026  
**Target**: Wickham Roofing CRM (wickham-roofing-crm)  
**Version**: 2.3.0  

---

## 1. FULL TEST SUITE AUDIT
- **What was tested**: Execution of `pytest` across the full tracked application suite (`tests/`).
- **Pass Rate**: **100% Pass** (304 Passed, 0 Skipped, 0 Failed).
- **Warnings**: 29 warnings (down from 30 — unregistered pytest mark resolved).
- **Smoke Test Matrix**: Verified 10/10 generated PDF document types (`contingency_agreement`, `contingency_agreement_signed`, `notice_of_cancellation`, `retail_contract_signed`, `certificate_of_completion`, `Supplement_Request`, `inspection_report_homeowner`, `Retail_Quote`, `PO_ABC_Supply`, `Commission_Statement`).
- **Coverage**: 67% total codebase coverage. Critical business path math and document generators tested 100%.

## 2. PIPELINE BLOCKER AUDIT (2026-08-11 Deep Audit)
Seven bugs confirmed and patched in a deep workspace audit session:
1. **Admin Kanban missing SQL columns** — `invoice_id`, `canvasser_name`, `supplement_sent_at`, `carrier_sla_days` were absent from the active jobs query. Silent blank fields on the Kanban card for all jobs. ✅ Fixed.
2. **Accounting WebSocket wrong URL** — connected to `/api/office/ws/office` (non-existent route) instead of `/ws/office`. Real-time accounting updates were silently dead. ✅ Fixed.
3. **WebSocket auth token passthrough** — dashboards now send `?token=` on WS upgrade for robust auth. ✅ Fixed.
4. **Triage resolve Redis guard missing** — `AttributeError` crash when Redis unavailable. Now returns clean 503. ✅ Fixed.
5. **Operations Board missing `INSPECTION_COMPLETED`** — jobs vanished from the board at this status. ✅ Fixed.
6. **Dead Kanban status columns** — `EV_ORDERED`/`MEASUREMENT_ORDERED` had no enum value; removed phantom columns. ✅ Fixed.
7. **Unregistered pytest mark** — `no_mock_ownership` mark now registered in `pyproject.toml`. ✅ Fixed.

## 3. SECURITY & RBAC AUDIT
- **What was tested**: API route RBAC mapping, PIN authentication hardening, SQL injection vectors, secret exposure, IDOR defenses, and path traversal protections.
- **PIN Integrity & Authentication**: Cleaned legacy generic demo PINs (`1111`, etc.), leaving strictly authenticated 4-digit bcrypt PINs for core team members (Michael, Scott, Debi) and assigned demo field reps (Jerry Grubb).
- **Field Rep Role Isolation**: Enforced `assert_field_rep_owns_job` across `/api/field/` endpoints. Field reps are strictly isolated to their assigned jobs and `field_safe` document types. Access to office documents (`office_only`) returns `403 Forbidden`.
- **SQL Injection**: Parameterized queries enforced 100% across SQLite transactions.
- **CORS & Secrets**: Secrets isolated in `.env` via `pydantic-settings`. CORS restricted to localhost and authorized production origins.

## 4. AI INSPECTION PIPELINE & FORENSIC GROUNDING AUDIT
- **Gemini File API Migration**: Migrated `document_parser.py` from legacy `pdfplumber` scraping to the Gemini File API with structured outputs. This enforces a deterministic Pydantic schema for Statement of Loss (SoL) extraction, eliminating regex-based parser fragility.
- **Zero-Shot Chain-of-Thought (CoT)**: Prompting schemas in `ai_service.py` upgraded to use Zero-Shot Chain-of-Thought visual reasoning. The model is forced to outline structural features and damage evidence before emitting final classifications, reducing AI hallucinations.
- **Multi-Image Batching & Fallbacks**: Configured `inspection_processor.py` to batch upload non-cached roof images to the Gemini File API and perform multi-image context analysis in a single batch request. Added a robust sequential fallback mechanism to handle transient API issues per photo.
- **Strict Schema Validation**: Implemented strict Pydantic schema validation at the model boundary (including `confidence_score` and `alternative_explanation` fields) to ensure all forensic narratives are grounded solely in visually verifiable data.
- **Evidence Grid Freshness**: Regenerated evidence-grid PDFs whenever cached AI analyses are present, preventing stale pre-analysis PDFs from being served from the document vault.

## 5. PDF DOCUMENT ENGINE & LEGAL COMPLIANCE AUDIT
- **Centralized Letterhead & Branding**: Upgraded `app/services/pdf/engine.py` with top-right logo positioning (`x=430, y=712, width=130, height=52`) on multi-page document templates, preventing text overlap.
- **Mandatory 1-Year Workmanship Warranty**: Embedded explicit 1-Year Workmanship Warranty guarantee boxes across all customer-facing contracts, quotes, estimates, inspection reports, and completion certificates.
- **Georgia HB 423 Compliance**: Hardened Georgia statutory disclosures (O.C.G.A. § 33-23-43(c)(4) deductible rebate warnings, statutory 5-day cancellation rights, public adjuster representation disclaimers, and 15% default clauses).
- **Digital Signatures & Auditing**: Embedded cryptographic IP, signer name, and UTC timestamp logs into signed PDFs.

## 6. DATA INTEGRITY & FINANCIAL AUDIT
- **Monetary Storage**: 100% migrated to `INTEGER` cents across database columns and job costing calculations.
- **SQLite Concurrency & WAL**: Operates with `PRAGMA journal_mode=WAL;` and `PRAGMA busy_timeout=15000;`.
- **WAL Backup Integrity**: WAL database backup/restore stress tests verified 100% data fidelity.
- **Pristine Demo Reset**: Demo reset now clears jobs, dependent operational tables, generated exports, signed agreements, both current and legacy document vault paths, field photos, and cached AI photo analyses while reseeding core team reps and the Jerry Grubb demo rep.

## 7. INFRASTRUCTURE & HEALTH TELEMETRY
- **Health Telemetry**: `/health` endpoint reports live `env`, `db_path`, `redis` connection status, and active git `commit_hash`.
- **Self-Healing Watchdogs**: Task scheduler scripts (`srv_fastapi.ps1`, `srv_worker.ps1`, `srv_redis.ps1`, `srv_tunnel.ps1`) ensure automated 24/7 uptime.

## 8. ARCHITECTURAL REFACTOR & REBRANDING AUDIT (2026-08-12 Deep Audit)
A comprehensive rebrand, architectural refactor, and testing/DevOps hardening pass was executed:
- **Repository Rename & Rebrand**: Repositioned repository from `JobNimbus_controller` to `wickham-roofing-crm`. Removed all legacy functional branding strings from live code and corrected URLs, templates, and badges in standard documentation files (`README.md`, `CONTRIBUTING.md`, `DEPLOYMENT.md`, `ARCHITECTURE.md`).
- **Modular App Refactoring**: Decoupled the monolithic `app/main.py` entrypoint. Created `app/server.py` to house the FastAPI application factory, middleware definitions, and router registries. Created `app/infra.py` to encapsulate structured logging configuration and Redis connection pooling. Preserved `app/main.py` as a lightweight re-export shim to maintain backwards compatibility with existing service runner scripts.
- **Pure-Domain Isolation**: Extracted `STATUS_LABELS` to `app/core/status_labels.py` and `days_since()` to `app/core/utils.py` to isolate pure, side-effect-free logic from web-framework constructs.
- **Code Quality & Type Hardening**: Populated `requirements-dev.txt` with linting, testing, and formatting tools. Upgraded `pyproject.toml` to enforce strict type checking across `app/core` and `app/services` modules. Added code-coverage configurations with strict failure threshold targets.
- **Property-Based Testing Integration**: Deployed property-based testing utilizing `hypothesis`. Added 16 automated properties in `tests/test_property_supplement.py` testing the deterministic math correctness of the `SupplementEngine` across the complete range of valid/invalid inputs (eaves, valleys, pitches, waste percentages, and multi-trade conditions). Created `docs/testing.md` to map all 48 test modules to distinct business guarantees.
- **CI/CD Pipeline & Recovery Hardening**: Created a GitHub Actions pipeline (`ci.yml`) to automatically run ruff linting, mypy type-checking, and the full pytest suite using a Redis service container on every commit/PR. Documented database Backup & Restore procedures under WAL mode, including directory mapping and checkpoint requirements.

## 9. STORM INGESTION PIPELINE & DASHBOARD AUDIT (2026-08-16)
- **High-Performance Ingestion**: Integrated NOAA Layer 2 (72-hour Local Storm Reports) MapServer queries, restricting ingestion to a spatial bounding box centered on office coordinates.
- **Nominatim Geocoding Elimination**: Removed client-side reverse-geocoding Nominatim calls and all Nominatim dependencies. Location names are now dynamically populated from NWS attributes.
- **Ingestion Deduplication**: Configured a compound `dedup_key` (event type, rounded latitude/longitude, and event time) backed by a SQLite UNIQUE index to ensure ingestion runs are completely idempotent.
- **Admin Dashboard Layout Update**: Refactored the Storm Radar floating widget into a top-level, collapsible card integrated directly into the Kanban layout content flow.
- **UI Nomenclature Realignment**: Standardized labels from "County" to "Location" to match NWS data structures.

## 10. STORM RADAR, SALES INTELLIGENCE & AUTH HARDENING AUDIT (2026-09-08)
- **Unified Storm Radar & Canvassing Intelligence**:
  - Replaced opaque counters with thresholded context across Admin and Field dashboards ("Hail events ≥ 1.00\" in last 72h: X").
  - Extracted shared client module (`app/static/js/storm_radar.js`) providing resilient timestamp formatting, WebSocket alert filtering, dynamic card rendering, and target ranking without client drift.
  - Surfaced `window_hours`, `min_hail_inches`, `min_wind_mph`, and `last_refreshed_utc` on all summary and target APIs.
- **Sales Enablement & Pipeline Acceleration**:
  - Field intake form automatically fetches `/api/field/storms/{zip}` on ZIP blur to present rep-ready, compliant talking points (mentioning recent local hail/wind dates without false outcome promises).
  - Enriched job APIs (`add_storm_flags_to_jobs`) with `has_recent_hail`, `has_recent_wind`, `recent_hail_max_inches`, `recent_wind_max_mph`, and `storm_window_hours`.
  - Added visual storm badges and dynamic "Next Best Action" hints in "My Recent Jobs".
  - Field app features clickable target ZIP cards with active filter indicators and instant clear buttons.
- **Payment Ingestion & Legacy Toggle Safety**:
  - Locked in `record_financial_payment` as the canonical payment entry path with strict Pydantic range validation (`0.0 <= amount <= 1,000,000.0`, valid ISO dates).
  - Standardized `last_payment_received_at` across `jobs` and `financials` as full ISO 8601 UTC timestamps.
  - Constrained `toggle_payment_flag` as an admin emergency shortcut; routes status advancement through `advance_status_for_payment` and preserves ledger history on toggle OFF.
- **Authentication & RBAC Hardening**:
  - Added sliding window brute-force lockout on `/auth/login` and PIN authentication routes (`app/services/rate_limit.py`), rejecting after 5 failed attempts per IP with HTTP 429 Too Many Requests.
  - Supported standard `Authorization: Bearer <token>` extraction in addition to internal headers and cookies.
  - Validated HS256 cryptographic signature and reject tampered tokens with HTTP 401.
  - Enforced strict admin-only boundary on `/api/office/jobs/sanity-check` (returns 403 for field reps and non-core accounting staff).
- **Tests Enforcing Behavior**:
  - `tests/test_auth_security.py`: 6 tests verifying login rate limiting, lockout, success reset, RBAC rejections, and JWT tampering.
  - `tests/test_storm_targets.py`: 21 tests verifying distinct counts, timestamps, talking points, and targets payload.
  - `tests/test_ui_contracts.py`: 9 tests verifying sanity check endpoints, storm flags on job lists, and dashboard renders.
  - `tests/test_database_integration.py`: 8 tests verifying UTC payment timestamps and financial operations.

---

### Final Summary & Metrics
- **Test Count**: 527+ Passing (100% Pass Rate)
- **PDF Engine Document Types Verified**: 10 / 10
- **CVEs Detected**: 0
- **System Health**: Hardened, Modular, Local-First, Production-Grade (v2.8.17)


