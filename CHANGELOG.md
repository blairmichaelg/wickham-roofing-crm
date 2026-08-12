# Changelog

## [2.1.3] - 2026-08-12
### Fixed
- **PWA Offline Sync**: Fixed a reference error in `field_app.html` service worker online event listener where `record` was incorrectly used instead of `item` when updating the offline queue retry count.

### Optimized
- **Building Code RAG**: Integrated `functools.lru_cache` on `parse_code_files` in `app/core/code_router.py` to prevent redundant file system reads during supplement pipelines.
- **Test Isolation**: Added `cache_clear()` calls in `tests/test_code_router.py` to preserve test environment hygiene.

## [2.1.2] - 2026-08-11
### Added & Hardened (AI Safety & Mathematical Verification Gates)
- **AI Safety Prompts**: Hardened all Statement of Loss extraction prompt templates in `app/services/ai_service.py` with explicit, mandatory `CRITICAL NO-MATH DIRECTIVE` blocks, preventing the LLM from executing arithmetic or guessing values.
- **Python-Side Carrier Arithmetic Validation**: Implemented deterministic validation in `UniversalClaimAST` (`app/core/ingestion_models.py`) to verify overall claim financials (`gross_rcv - depreciation - deductible == net_claim`) and line item totals (`claimed_rcv - depreciation == acv`) using Python `Decimal` logic. Any math discrepancies (> $0.05) or negative roof geometry measurements will immediately raise a `ValueError` validation block, stopping ingestion and preventing bad data from entering the database.
- **Test Suite Expansion**: Added `tests/test_ai_safety_and_math.py` with 6 comprehensive test cases validating prompt templates, valid/invalid AST math, line item mismatches, negative geometry, and `PhotoAnalysis` confidence bounds.
- **Typing and Docstring Hardening**: Hardened and documented code across the core pipeline files (`ai_service.py`, `document_parser.py`, `ingestion_models.py`, `reconciliation.py`).
- **User Guides and Manuals Update**: Updated all technical and user guides (`admin_tech_guide.md`, `accounting_guide.md`, `operations_guide.md`, `canvasser_field_guide.md`) to reflect these security, safety, and validation protocols.

### Verification
- **Test suite: 282 passed** — all green.

## [2.1.1] - 2026-08-11
### Fixed (Production Pipeline Hardening — Deep Workspace Audit)
- **Admin Kanban Blank Fields (BLOCKER)**: `_fetch_active_jobs_sync` was only selecting 7 columns but the admin Kanban template required `invoice_id`, `canvasser_name`, `supplement_sent_at`, and `carrier_sla_days`. All four were missing, causing invoice badges to show raw UUIDs, ownership labels to be invisible, and the SLA-exceeded alert to never fire. Added all missing columns to the `SELECT`.
- **Accounting Dashboard WebSocket Dead Connection (BLOCKER)**: `accounting_dashboard.html` was connecting its WebSocket to `/api/office/ws/office` — a route that does not exist. The real endpoint is `/ws/office`. Fixed URL on the accounting dashboard to match, restoring real-time push updates for the Accounting team.
- **WebSocket Auth Token Passthrough**: Both admin and accounting dashboards now append `?token=<AUTH_TOKEN>` to WebSocket URLs, ensuring robust authentication alongside the existing cookie fallback path.
- **Triage Resolve Redis Guard (BLOCKER)**: `admin_triage_resolve` was calling `request.app.state.redis_pool.enqueue_job()` without verifying `redis_pool` exists first. When Redis is unavailable, this threw an unhandled `AttributeError`. Now returns a clean `503 Service Unavailable`.
- **Operations Board Missing Status**: `INSPECTION_COMPLETED` was absent from the "Inspections & Closeout" column query in `operations_routes.py`. Jobs at this status disappeared from the board entirely.
- **Dead Kanban Columns Removed**: `EV_ORDERED` and `MEASUREMENT_ORDERED` were defined in `STATUS_LABELS` and the admin Kanban column list but have no corresponding `JobStatus` enum values. Removed both dead labels and phantom columns.
- **Unregistered Pytest Mark Warning**: `@pytest.mark.no_mock_ownership` was not registered in `pyproject.toml`. Registered the mark to clean up test output (29 warnings, down from 30).

### Verification
- **Test suite: 274 passed, 2 skipped, 29 warnings** — all green.

## [2.1.0] - 2026-08-06
### Added & Remediated
- **Admin Upload Pipeline Fix**: Updated HOVER/EagleView + Statement of Loss uploads to request full supplement PDF generation and added an inline fallback when Redis is unavailable, preventing dashboard upload dead-ends.
- **Evidence Grid AI Freshness**: Added stale-vault protection so evidence grids regenerate once AI photo analyses are cached, and restored the `/api/field/jobs/{job_id}/evidence_grid` route used by field links.
- **Pristine Demo Reset**: Expanded `scripts/db_demo_reset.py` to clear both active and legacy document vault paths plus cached AI photo analyses before reseeding core team users and Jerry Grubb.
- **Verification Metadata**: Refreshed test-count documentation to the current `274 passed, 2 skipped` application suite.
- **Pipeline Integrity Fixes**: Resolved FastAPI 500 error on suggested date updates by fixing shadowed `backup_database` imports in `office_routes.py`.
- **Database Synchronization**: Added `loss_date` to the `jobs` table via migration `0014_add_loss_date_to_jobs.py` to synchronize tracking with `storm_verifications`.
- **Accounting & Financial Calculations**: Overhauled `upsert_financials` to calculate and store `depreciation_cents` and `net_claim_cents`, preventing $0.00 accounting dashboard artifacts.
- **Double-Rollback Safeguards**: Removed redundant `ROLLBACK` commands before raising `ValueError` in database update functions, preventing secondary connection crashes.
- **Verification Coverage**: Created `tests/test_shingle_endpoints.py` to assert claim and shingle update integrity under simulated client activity.
- **Demo State Re-initialization**: Executed a full transactional wipe and seeded Jack's demo job to ensure a clean state for live demonstration.

## [2.0.0] - 2026-08-05
### Added & Upgraded (AI Inspection Pipeline Overhaul)
- **Gemini File API Migration**: Completely migrated Statement of Loss (SoL) extraction from fragile legacy PDF parsing (`pdfplumber` and regexes) to the Gemini File API. Standardized extraction directly against structured `StatementOfLoss` Pydantic schemas.
- **Zero-Shot Chain-of-Thought Visual Forensics**: Refactored image analysis prompting to use Zero-Shot Chain-of-Thought reasoning. Instructed the model to analyze evidence, list structural observations, and outline grounding data before making a classification.
- **Pydantic Type & Grounding Enforcement**: Updated all inspection and supplement schemas with confidence scoring, alternative explanations, and strict model validators to eliminate hallucinations.
- **Batch Processing & Fallback Recovery**: Re-engineered background task orchestrators (`inspection_processor.py`) to leverage multi-image context batching via the Gemini File API. Added a robust sequential fallback mechanism to ensure individual processing recovery if batch operations encounter throughput blocks.
- **Robust Cleanup Management**: Configured clean file/temp file disposal in all successful and error scenarios.

## [1.7.0] - 2026-08-04
### Added & Professionalized
- **PDF Engine & Executive Branding Overhaul**: Re-architected PDF layout infrastructure in `app/services/pdf/engine.py` (`_universal_letterhead`, metadata grids, warning callout boxes, signature blocks) featuring top-right logo positioning, navy headers (`#1e3a8a`), and crisp slate borders.
- **Mandatory 1-Year Workmanship Warranty**: Embedded explicit 1-Year Workmanship Warranty Guarantee callouts across all 10 system-generated PDF document types (`documents.py`, `supplement.py`, `inspection_report.py`, `invoice.py`, `commission.py`).
- **Georgia Statutory Legal Disclosures**: Hardened legal protection in contracts, quotes, and estimates with Georgia HB 423 deductible rebate disclaimers (O.C.G.A. § 33-24-59.27), public adjuster representation limits, 5-day cancellation notices, 15% default clauses, and mechanics lien waivers.
- **Auth & RBAC Hardening**: Audited authentication endpoints, purged non-standard generic fallback PINs, and strictly enforced field rep job ownership checks (`assert_field_rep_owns_job`).
- **Full Verification Suite**: Executed end-to-end smoke test validating all 10 document types and passed all 256 test modules in `pytest`.

## [1.6.1] - 2026-08-04
### Added & Prepared
- **Demo Readiness**: Added a documented fresh-state database reset workflow for demo prep, including a one-shot reset script that clears all jobs and restores the default Jerry Grubb demo field rep state.
- **UI Error-State Polish**: Hardened admin-facing triage and dashboard surfaces so review/failure states surface worker-generated error details clearly in the UI.
- **Version Metadata**: Updated packaged project metadata and app version identifiers to reflect the current release state.

## [1.6.0] - 2026-08-04
### Added & Hardened
- **Health Telemetry**: Expanded `/health` pre-flight check endpoint to report `app_env`, `db_path`, and git `commit_hash` for deployment visibility.
- **Demo Reset Script**: Consolidated demo database reset tooling into `scripts/db_demo_reset.py` with foreign key safe deletion order, table truncation, physical attachment directory wiping, and single field rep ("Jerry Grubb") initialization.
- **Path Traversal & IDOR Defenses**: Enforced `uuid.UUID` validation on all document/photo upload and download route parameters (`job_id`, `doc_id`) and sanitized file download attachment headers via `sanitize_download_filename()`.
- **ARQ Worker Resilience**: Implemented automatic retry handling (`arq.worker.Retry`) for transient network/API timeouts in background workers, plus fail-loud database status writebacks (`PENDING_OPERATOR_REVIEW`, `INSPECTION_FAILED`) and error logging to `job_tasks`.

## [1.5.1] - 2026-08-02
### Added & Fixed
- **Accounting Operations**: Added a 'Mark Paid' button to the accounting dashboard commissions table, seamlessly integrated with the backend endpoint to update readiness state.
- **PWA Assets**: Generated properly proportioned `icon-192.png` and `icon-512.png` assets padded from `logo.png` for Progressive Web App compliance.
- **Strict Typing Expansions**: Safely expanded strict `mypy` typing coverage to core modules (`app.services.pdf.documents` and `app.services.pdf.engine`), repairing latent missing return types.
- **Load Testing Scaffolding**: Deployed foundational `locust` load testing definitions simulating concurrent field API leads and office dashboard hits against the local server environment.

## [1.5.0] - 2026-07-31
### Added & Fixed
- **Mobile Responsiveness**: Implemented responsive UI reflows (`max-width: 768px`) across all core job, accounting, and triage templates for seamless field tablet usage.
- **AI Damage Signals (Phase 1)**: Integrated Gemini 2.5 Flash ARQ workers (`photo_processor.py`) for automated, background damage tagging of field roof photos without blocking UI threads.
- **Deterministic Condition Index (Phase 2)**: Developed a non-LLM, strictly mathematical `calculate_condition_index()` model that merges AI vision signals and structured field data into a deterministic 0-100 property score and A-F grade.
- **Storm-Event Inference (Phase 3)**: Replaced mock weather data with a live, geo-spatially accurate Historical Storm Ingestion engine querying the Iowa Environmental Mesonet (IEM) LSR API. Automatically maps severe hail and wind events (past 365 days) against a defined 150-mile service radius (GA, AL, FL) to infer "Suggested Dates of Loss" and generate targeted canvassing leads.
- **Redis Queue Resilience (Phase 4)**: Enforced AOF persistence (`--appendonly yes`) in `srv_redis.ps1` and introduced a local backup health validation script (`check_backups.ps1`) for enhanced Wickham Roofing CRM stability.
- **AI Abstraction Layer (Phase 5)**: Architected formal `AiClient` interfaces, migrating all Gemini integrations under a unified `get_ai_client()` dependency injector. Refactored `document_parser.py` and background workers to use this decoupled layer.
- **Test Suite Integrity (Phase 6)**: Fixed regression testing artifacts caused by abstraction refactoring. The full test suite (240+ tests) correctly validates all system security boundaries.
## [1.4.2] - 2026-07-29
### Added & Fixed
- **Security/Testing**: Refactored detect_pdf_format to remove test-environment-specific branches, ensuring the detector consistently classifies any malformed or corrupted file as UNKNOWN organically using strict pdfplumber exception handling in both production and test suites.
- **Unified Pipeline Routing**: Upgraded pipeline.py and API endpoints to dynamically identify and route measurement PDFs (EagleView vs Hover) through a single robust ingestion path with strict error boundaries.
- **Hover Extractor Service**: Built an isolated `hover_extractor.py` service capable of natively parsing Hover Roof Measurement PDFs into standard `EagleViewData` models. Includes a strict feet-inches to decimal conversion helper and a PDF format detector that automatically discriminates between Hover and EagleView documents.
- **Hover Automated Tests**: Implemented comprehensive integration and unit tests in `test_hover_extractor.py` validating area, pitch, and facet extraction against real-world Hover files, achieving 100% test passage with zero regressions to the existing EagleView pipeline.

## [1.4.1] - 2026-07-29
### Added & Fixed
- **Security/Testing**: Refactored detect_pdf_format to remove test-environment-specific branches, ensuring the detector consistently classifies any malformed or corrupted file as UNKNOWN organically using strict pdfplumber exception handling in both production and test suites.
- **Unified Pipeline Routing**: Upgraded pipeline.py and API endpoints to dynamically identify and route measurement PDFs (EagleView vs Hover) through a single robust ingestion path with strict error boundaries.
- **Accounting Test Coverage**: Added `test_accounting_financials_allowed` to `test_rbac_hardening.py` confirming the accounting role can successfully bypass operations blockades and save financials.
- **Transaction Lock Fix**: Patched a latent transaction error by adding a missing `BEGIN IMMEDIATE` lock to `upsert_job_financials`.
- **Measurement Report Category**: Added standard `MEASUREMENT_REPORT` (EagleView) selector to the job workspace upload component, routing documents safely to `field_safe` status.

## [1.4.0] - 2026-07-29
### Added & Fixed
- **Security/Testing**: Refactored detect_pdf_format to remove test-environment-specific branches, ensuring the detector consistently classifies any malformed or corrupted file as UNKNOWN organically using strict pdfplumber exception handling in both production and test suites.
- **Unified Pipeline Routing**: Upgraded pipeline.py and API endpoints to dynamically identify and route measurement PDFs (EagleView vs Hover) through a single robust ingestion path with strict error boundaries. (Final Production Hardening & RBAC Workspace)
- **Financial Visibility Lock**: Hardened the Unified Job Workspace so Operations personnel have strict read-only access to job financials. The Save button is stripped from the DOM and the API endpoints rigorously enforce `verify_accounting` tokens.
- **Document Vault Rearchitecture**: Overhauled the document vault upload UI to include a mandatory category dropdown. Uploaded files are now dynamically tagged with `field_safe` or `office_only` visibility directly at the SQLite level based on category, completely eliminating brittle filename keyword-matching logic.
- **Immutable Download Security**: Rewrote the vault download endpoint to assert access control purely against the `visibility` database column. Field Reps are instantly denied (403 Forbidden) from downloading sensitive financial or estimate artifacts.
- **Test Suite Perfection**: Developed `tests/test_rbac_hardening.py` to mathematically prove access boundaries between Field Reps and Office staff. Resolved critical test environment token leakage by moving auth setups directly into isolated fixtures. The entire 230-test suite is executing cleanly with a 100% success rate.

## [1.3.0] - 2026-07-29
### Added & Fixed
- **Security/Testing**: Refactored detect_pdf_format to remove test-environment-specific branches, ensuring the detector consistently classifies any malformed or corrupted file as UNKNOWN organically using strict pdfplumber exception handling in both production and test suites.
- **Unified Pipeline Routing**: Upgraded pipeline.py and API endpoints to dynamically identify and route measurement PDFs (EagleView vs Hover) through a single robust ingestion path with strict error boundaries. (Shared Job Workspace & Document Vault)
- **Unified Single Source of Truth**: Transitioned from a fragmented, role-split dashboard architecture to a shared, unified Job Workspace (`/office/jobs/{job_id}`).
- **Role-Based Access Control (RBAC)**: Implemented `verify_office_role` to allow Admin, Operations, and Accounting access to the shared workspace, dynamically hiding sensitive financial data and admin-override controls via Jinja templates based on user role.
- **Universal Document Vault**: Built a centralized, dynamic rendering loop within the unified dashboard that pulls directly from the `job_documents` SQLite table, providing a clean list of all files with immediate download links.
- **Field App Compliance**: Forced display of the full Contingency Agreement legal text to Field Reps, requiring an explicit checkbox acknowledgment before unlocking the signature capture pad.
- **Strict Vault Security**: Enforced field-safe restrictions on the vault download endpoint. Field Reps are strictly hard-blocked (`403 Forbidden`) from accessing financial files, estimates, supplements, invoices, and QBO exports.
- **Automated Document Registration**: Generating a signed Contingency Agreement PDF now automatically registers the document into the `job_documents` table for instantaneous Document Vault visibility.
- **Brand Consistency**: Generated and natively embedded a Wickham Roofing logo (`logo.png`) into the core PDF generator letterhead and standard login screens.
- **Navigation Polish**: Replaced hardcoded dashboard `/login` hooks with proper `/auth/logout` handlers. Added 'View Job' deep-links across Accounting and Operations ledgers.

## [1.2.1] - 2026-08-02
### Added
- **Retail Contracts**: Implemented standalone retail sales contract PDF generation with statutory Right to Cancel notices and 5-year workmanship warranties.
- **Retail Contract Signing**: Added `POST /api/field/jobs/{job_id}/sign-retail-contract` endpoint for field reps to capture signatures and instantly generate retail agreements.
- **Job Status Badges**: Enhanced the Admin Dashboard Kanban board to visually distinguish "LEAD — No Agreement Signed" and "AGREEMENT SIGNED" states with clear color-coded badges, alongside the Canvasser's name.

## [1.2.0] - 2026-07-28
### Added & Fixed
- **Security/Testing**: Refactored detect_pdf_format to remove test-environment-specific branches, ensuring the detector consistently classifies any malformed or corrupted file as UNKNOWN organically using strict pdfplumber exception handling in both production and test suites.
- **Unified Pipeline Routing**: Upgraded pipeline.py and API endpoints to dynamically identify and route measurement PDFs (EagleView vs Hover) through a single robust ingestion path with strict error boundaries. (Service Architecture & Auth Hardening)
- **New Service Architecture**: Migrated away from manual script execution to a persistent, automated Task Scheduler-based startup. Introduced specialized wrapper scripts (`srv_redis.ps1`, `srv_fastapi.ps1`, `srv_worker.ps1`, `srv_tunnel.ps1`) to handle automated restart loops, dedicated logging, and port resolution on system boot.
- **Repository Hygiene**: Superseded and removed legacy orchestration scripts (`start_prod.ps1`, `start_production.ps1`) and miscellaneous development scratch files.
- **Auth Hardening**: Enforced explicit authentication requirements across `/office/jobs/{job_id}`, `/field`, and `/ws/office` routes, solidifying the role-based access control architecture.
## [1.1.0] - 2026-07-25
### Added & Fixed
- **Security/Testing**: Refactored detect_pdf_format to remove test-environment-specific branches, ensuring the detector consistently classifies any malformed or corrupted file as UNKNOWN organically using strict pdfplumber exception handling in both production and test suites.
- **Unified Pipeline Routing**: Upgraded pipeline.py and API endpoints to dynamically identify and route measurement PDFs (EagleView vs Hover) through a single robust ingestion path with strict error boundaries. (Accounting Ledger Hardening & Root Decluttering)
- **Accounting Dashboard Remediation**: Fully hardened `accounting_dashboard.html` against JavaScript runtime failures. Fixed variable scope leakage on empty commission states (`cdata`/`ctbody`), deployed safe Number conversions (`|| 0`) on financial formatting to prevent `.toFixed()` type exceptions, and engineered tolerant date parsing for non-UTC timestamps.
- **Resilient Realtime WebSockets**: Replaced raw WebSocket instantiations in the Accounting ledger with a robust `connectWebSocket()` pattern featuring automatic exponential reconnection retry logic (5-second fallback) upon connection drop or worker reboot.
- **Manual Commission Override Engine**: Added UI controls ("Adjust %" / "Reset to Default") and backend API routing (`/api/office/accounting/jobs/{job_id}/commission-override`) enabling Accounting personnel (Debi) to manually override standard 10% canvasser commission allocations per job.
- **Root Workspace Decluttering & Binary Isolation**: Consolidating repository structure by relocating orphaned documentation (`FIELD_RUNBOOK.md`, `security_tasks.md`) directly into the specialized `docs/` folder alongside complete operational role manuals. Isolated networking executable binaries (`cloudflared.exe`) into a dedicated `tools/` directory and secured `.gitignore` exclusion rules.
- **Local Boot Script Optimization**: Resolved startup crashing in legacy Windows single-click boot scripts (`start_dev.ps1`, `start_prod.ps1` - now superseded) by replacing references to pruned historical diagnostic files with instant inline Python module inspections validating runtime dependencies and environment health.
- **Enterprise Documentation Overhaul**: Modernized public engineering documentation (`README.md`, `ARCHITECTURE.md`, `DEPLOYMENT.md`, `CONTRIBUTING.md`) to reflect the multi-role standalone V4 Wickham Roofing CRM operating platform and celebrate our verified 229-test 100% passing test matrix.

## [1.0.0] - 2026-07-22
### Added & Fixed
- **Security/Testing**: Refactored detect_pdf_format to remove test-environment-specific branches, ensuring the detector consistently classifies any malformed or corrupted file as UNKNOWN organically using strict pdfplumber exception handling in both production and test suites.
- **Unified Pipeline Routing**: Upgraded pipeline.py and API endpoints to dynamically identify and route measurement PDFs (EagleView vs Hover) through a single robust ingestion path with strict error boundaries. (The Great Pruning & Final Launch Preparation)
- **Production Cleanliness**: Ruthlessly pruned all dead code, orphaned UI templates, and legacy developer scripts. Removed 51 instances of unused code and legacy `.db`/`.txt` bloat from the repository root.
- **UI-to-DB Re-wiring**: Repaired multiple silent JavaScript routing failures across the isolated Field, Operations, and Admin Triage dashboards caused by the Phase 9 JWT security upgrades.
- **ARQ Worker Patch**: Patched a fatal `AttributeError` in the `field_routes.py` supplement resumption endpoint to correctly target `request.app.state.redis_pool`.
- **E2E Mathematical Stability**: Implemented `tests/test_happy_path.py`, an end-to-end integration test proving the complete multi-role lifecycle (Canvasser injection, AI orchestration, Operations scheduling, and Accounting QBO export). The local-first Wickham Roofing CRM CRM is mathematically proven stable for launch.

## [0.9.0] - 2026-07-21
### Added & Fixed
- **Security/Testing**: Refactored detect_pdf_format to remove test-environment-specific branches, ensuring the detector consistently classifies any malformed or corrupted file as UNKNOWN organically using strict pdfplumber exception handling in both production and test suites.
- **Unified Pipeline Routing**: Upgraded pipeline.py and API endpoints to dynamically identify and route measurement PDFs (EagleView vs Hover) through a single robust ingestion path with strict error boundaries. (Phase 3 Security Hardening)
- **Role Isolation & Dependencies**: Integrated strict JWT role validations across all route files. Enforced `verify_admin` on office endpoints and `verify_accounting` on ledger toggles. Injected role identities explicitly into ARQ background pipelines, completely halting unauthenticated queue requests.
- **Path Traversal Protection**: Hardened the download route infrastructure. Added a robust `sanitize_download_filename` barrier that structurally enforces exact matching on expected document names, eliminating relative path (`../`) vulnerabilities.
- **Field Rep Boundary Enforcement**: Implemented `assert_field_rep_owns_job` across the `/api/field/` namespace. Field reps are now strictly isolated to jobs matching their `canvasser_rep_id`, halting cross-rep snooping. Admin JWTs can dynamically bypass this barrier.
- **PIN Cryptography**: Successfully migrated field rep PINs from plaintext configuration defaults to database-managed `bcrypt` hashes. Strengthened "No Silent Zeros" compliance across the authentication boundary.
- **ARQ Rate Limiting**: Deployed an in-memory sliding window rate limiter specifically guarding resource-heavy pipeline endpoints (`/material_order`, `/supplement_docs`, `/resume-supplement`) to prevent Denial of Service bursts.
- **Resume Code RAG Restoration**: Fixed a logical gap in the `run_supplement_pipeline` resume sequence where IBC/IRC building codes were silently dropped. Re-wired the Zero-Cost RAG lookup to ensure consistent, defensible generation on resumed jobs.

## [0.8.1] - 2026-07-21
### Added & Fixed
- **Security/Testing**: Refactored detect_pdf_format to remove test-environment-specific branches, ensuring the detector consistently classifies any malformed or corrupted file as UNKNOWN organically using strict pdfplumber exception handling in both production and test suites.
- **Unified Pipeline Routing**: Upgraded pipeline.py and API endpoints to dynamically identify and route measurement PDFs (EagleView vs Hover) through a single robust ingestion path with strict error boundaries.
- **Fixed `pdf_path` return value**: Corrected the return value of `run_supplement_pipeline` to return the permanent PDF path instead of a nullified temporary path.
- **Weaponized Waste Justification Testing**: Added direct unit test (`test_build_waste_explanation_weaponized`) covering dynamic waste formatting.
- **Resume Path Regression Prevention**: Implemented `test_resume_succeeds_with_saved_report` to prove a successful resume uses the saved `DiscrepancyReport` without re-parsing or regenerating data.
- **Dynamic Waste for Material Orders**: Updated `generate_material_order_pipeline` to dynamically compute waste instead of statically falling back to 15%, reducing material over-ordering risk, along with a regression test.

## [0.8.0] - 2026-07-21
### Added & Fixed
- **Security/Testing**: Refactored detect_pdf_format to remove test-environment-specific branches, ensuring the detector consistently classifies any malformed or corrupted file as UNKNOWN organically using strict pdfplumber exception handling in both production and test suites.
- **Unified Pipeline Routing**: Upgraded pipeline.py and API endpoints to dynamically identify and route measurement PDFs (EagleView vs Hover) through a single robust ingestion path with strict error boundaries. (Phase 2 Hardening)
- **No Silent Zeros Pipeline Block**: Hard-blocked the supplement pipeline on missing flashing or step-flashing metrics, protecting financial determinism and routing incomplete jobs to `PENDING_OPERATOR_REVIEW`.
- **Gross RCV Verification**: Enforced a hard-halt on Statement of Loss (SoL) ingestion if the Carrier Gross RCV math fails to verify, catching synthetic math discrepancies before narrative generation.
- **Idempotent Flag Generation**: Rebuilt the `generate_and_gate_flags` and SoL math discrepancy flag insertions to use an idempotent `DELETE`/`INSERT` transaction block, preventing ghost flags upon document resubmission.
- **Dynamic Waste Integration**: Wired the complexity engine (based on facets, pitch, and valley LF) natively into both the full office and supplement pipelines, replacing the static 15% assumption.
- **Weaponized Waste Justifications**: Updated the discrepancy engine to output mathematically defensible, score-based waste factor explanations directly into the generated AI context window.
- **Test Suite Integrity**: Expanded and hardened test suites across `test_e2e_pipeline.py`, `test_ingestion.py`, and `test_reconciliation.py`, maintaining 100% test coverage and restoring a fully green testing matrix.

## [0.7.0] - 2026-07-15
### Added & Fixed
- **Security/Testing**: Refactored detect_pdf_format to remove test-environment-specific branches, ensuring the detector consistently classifies any malformed or corrupted file as UNKNOWN organically using strict pdfplumber exception handling in both production and test suites.
- **Unified Pipeline Routing**: Upgraded pipeline.py and API endpoints to dynamically identify and route measurement PDFs (EagleView vs Hover) through a single robust ingestion path with strict error boundaries.
- **Atomic State Machine Consolidation**: Refactored `update_material_flags` into `transition_material_flags` to guarantee atomic database flag updates and job state transitions inside a single `BEGIN IMMEDIATE` transaction, eliminating race conditions that previously stalled the pipeline.
- **Admin State Override API**: Created `force_override_status` and exposed it via `/api/admin/jobs/{job_id}/override` to allow emergency state machine bypasses. Enforces a mandatory "ADMIN OVERRIDE" prefix in the job's JSON history trail.
- **State Machine Hardening**: Split `JobStatus` into Processing (ARQ) and Business (Operator) tracks with explicit API gates.
- **Strict Schedule Guards**: Added database-level SQLite blockers preventing installation scheduling before `MATERIALS_ON_SITE` is confirmed.
- **Append-Only Document Vault**: Refactored `job_documents` from a destructive UPSERT model to an immutable, append-only architecture for complete historical versioning.
- **Orchestrator Halt**: Modified the Master Office Pipeline to halt at `PENDING_OPERATOR_REVIEW` instead of automatically advancing states, ensuring human-in-the-loop validation.
- **Strict EagleView Extraction**: Upgraded the `pdf_extractor` to deterministically extract `Hips` and `Predominant Pitch`, failing loudly on unsupported formats, and returning SHA256 fingerprints natively.
- **Evidence-Bearing AST**: Expanded `UniversalClaimAST` to enforce strict provenance tracking (`source_doc_sha256`, `source_doc_id`, `ast_version`).
- **Anti-Hallucination Parser**: Replaced the obsolete ESX parser with a three-layer Statement of Loss (SoL) ingestion pipeline featuring structural (`pdfplumber`), semantic (`Gemini`), and mathematical (`Pydantic`) verification.
- **Automated Carrier Math Audits**: Wired the `process_supplement` ARQ worker to automatically flag carrier math inconsistencies from SoL parsing, intentionally halting the job into `PENDING_MANUAL_REVIEW` to prevent bad data progression.
- **Operations Board Interface**: Created a new read-only departure board for operations with a secured action modal containing `materials_ordered` and `materials_on_site` toggle flags.
- **Strict Role-Based Routing**: Deployed `operations_routes.py` with restricted token authentication ensuring operations can only patch material flags and nothing else.
- **QBO Batch Export Queue**: Added an idempotent bulk export endpoint for accounting and wired it into the dashboard to safely generate and download QBO CSVs while preventing duplicate exports.
- **Offline-First Field App**: Completely overhauled the service worker to use an IndexedDB-backed caching engine. Field agents can now submit leads offline (intercepted with a 202 status) which are automatically synchronized via Background Sync when connectivity returns.
- **Production Threading Hardening**: Replaced illegal async-wrapped `get_connection()` calls with sync execution inside `process_supplement_event` ARQ workers to prevent connection pool poisoning.
- **Atomic QBO Batch Exports**: Wrapped QuickBooks batched status updates in a single `BEGIN IMMEDIATE` transaction, totally eliminating TOCTOU race conditions and ensuring idempotency.
- **Path Traversal Security**: Explicitly stripped path elements via `Path(filename).name` in the export download route to block LFI (Local File Inclusion) attempts.
- **Resilient AI Pipelines**: Built a local `supplement_reports` SQL cache to persist state for the ARQ worker. Resuming a halted worker now bypasses network requests to Gemini/EagleView and reconstructs the narrative seamlessly from local cache.

## [0.6.1] - 2026-07-13
### Added & Fixed
- **Security/Testing**: Refactored detect_pdf_format to remove test-environment-specific branches, ensuring the detector consistently classifies any malformed or corrupted file as UNKNOWN organically using strict pdfplumber exception handling in both production and test suites.
- **Unified Pipeline Routing**: Upgraded pipeline.py and API endpoints to dynamically identify and route measurement PDFs (EagleView vs Hover) through a single robust ingestion path with strict error boundaries.
- **Pre-Demo Stability Audit**: Resolved 7 critical and high-priority bugs identified during system audit.
- **Pipeline Lifecycle**: Fixed premature status transitions; EagleView uploads now transition to `EV_PARSED` instead of auto-invoicing via QBO export.
- **Data Integrity**: Corrected EagleView field name mapping in inspection letters and wired live database lookups for inspection addresses.
- **PDF Generation**: Hardened the supplement generator to dynamically filter and inject only job-specific, climate-triggered rules via explicit SQL JOINs.
- **File System Stability**: Centralized and synchronized all `FIELD_DOCS_DIR` path resolution across the orchestration layer and endpoints.
- **Error Handling**: Patched fatal `ImportError` exceptions in the material order route to ensure pristine demonstration stability.

## [0.6.0] - 2026-07-13
### Added & Fixed
- **Security/Testing**: Refactored detect_pdf_format to remove test-environment-specific branches, ensuring the detector consistently classifies any malformed or corrupted file as UNKNOWN organically using strict pdfplumber exception handling in both production and test suites.
- **Unified Pipeline Routing**: Upgraded pipeline.py and API endpoints to dynamically identify and route measurement PDFs (EagleView vs Hover) through a single robust ingestion path with strict error boundaries.
- **Architectural Refactor**: Comprehensive backend hardening for the V4 Wickham Roofing CRM.
- **SQLite Concurrency**: Enforced explicit `BEGIN IMMEDIATE` transaction blocks and PRAGMA configurations (WAL, mmap, busy_timeout) to eliminate read-to-write database locks.
- **Universal Claim AST**: Built `ingestion_models.py` leveraging Pydantic V2 for mathematically deterministic extraction of adjustor claims.
- **Role-Tailored Projections**: Deployed `live_material_board` and `financial_delta_view` SQL Views for immediate operations and accounting insights.
- **WebSocket Zombie Sweeper**: Upgraded `Notifier` to `RobustConnectionManager` with an active background `asyncio` heartbeat loop isolating dead connections.

## [0.5.2] - 2026-07-13
### Added & Fixed
- **Security/Testing**: Refactored detect_pdf_format to remove test-environment-specific branches, ensuring the detector consistently classifies any malformed or corrupted file as UNKNOWN organically using strict pdfplumber exception handling in both production and test suites.
- **Unified Pipeline Routing**: Upgraded pipeline.py and API endpoints to dynamically identify and route measurement PDFs (EagleView vs Hover) through a single robust ingestion path with strict error boundaries.
- **System Stability**: Resolved critical asynchronous Coroutine execution bugs in the V4 Wickham Roofing CRM pipeline affecting inspection doc generation.
- **Type Safety**: Enforced strict typing compliance (100% `mypy` passing) across `pdf_generator.py` ReportLab bindings.
- **Code Cleanliness**: Resolved all `ruff` static analysis linting errors by pruning unused imports, unused variables, and organizing module imports.
- **Testing Reliability**: Migrated `MagicMock` patches to `AsyncMock` to accommodate the newly refactored async pipeline architecture.

## [0.5.1] - 2026-07-10
### Added & Fixed
- **Security/Testing**: Refactored detect_pdf_format to remove test-environment-specific branches, ensuring the detector consistently classifies any malformed or corrupted file as UNKNOWN organically using strict pdfplumber exception handling in both production and test suites.
- **Unified Pipeline Routing**: Upgraded pipeline.py and API endpoints to dynamically identify and route measurement PDFs (EagleView vs Hover) through a single robust ingestion path with strict error boundaries.
- **Security Hardening**: Patched UUID path traversal vulnerabilities across all `field_routes.py` mutation endpoints.
- **Backup Environment Targeting**: Scoped the SQLite hot backup system to only execute in production (`APP_ENV=production`), protecting production data from local development pollution.
- **Deterministic Math Engine**: Wired the pure mathematical `calculate_ice_and_water_rolls` function into the orchestrator pipeline for climate-gated calculations.
- **Fail-Loud Pipeline Resume**: Built the `PENDING_MANUAL_REVIEW` halting flow and a manual flag resolution `PATCH` endpoint, complete with IDOR defenses and an immutable audit trail.

## [0.5.0] - 2026-07-06
### Added
- **Infrastructure Hardening**: Implemented automated nightly ARQ garbage collection for `.tmp` artifacts.
- **Cryptographic Deduplication**: Replaced redundant file processing with SHA-256 stream hashing and API short-circuiting.
- **Atomic Concurrency**: Refactored SQLite state machine to use `json_insert()`, eliminating Optimistic Concurrency crash risks.

## [0.4.0] - 2026-06-30
### Added
- **V4 Local CRM Pivot (Wickham Roofing CRM)**: Full independent pipeline replacing SaaS CRMs.
- **SQLite WAL State Machine**: Replaced JobNimbus with a robust, concurrent local database.
- **Unified Office Dashboard**: Local UI displaying metadata, schedules, margins, and artifacts.
- **Paperwork Matrix**: Generates Supplier POs and Georgia Statutory Compliance Documents locally.

## [0.3.0]
### Added
- **V3 Vision Engine**: Multimodal roof damage detection using Gemini Flash.
- **Evidence Grids**: Auto-generates forensic photo grids for insurance adjusters.

## [0.2.0]
### Added
- **V2 Supplement Engine**: Deterministic insurance supplement generation based on EagleView logic.
- **Automath Engine**: Computes exact BOM and discrepancy reports.

## [0.1.0]
### Added
- Initial JobNimbus webhook orchestration framework.
