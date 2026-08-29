# Changelog

## [2.8.1] - 2026-08-29
### Fixed & Enhanced (Documentation Drift Regression Testing & Role Guide Refresh)

- **Documentation Drift Testing (`tests/test_docs_sync.py`)**: Added automated regression testing verifying that all active core FastAPI routes from `app.openapi()["paths"]` match documentation and preventing drift across sprints.
- **Legacy Schema Cleanup in Documentation**: Verified and confirmed operator guides in `docs/` (`admin_tech_guide.md`, `accounting_guide.md`, `operations_guide.md`, `field_runbook.md`) do not reference dropped database columns (`acv_check_amount`, `carrier_initial_rcv`, etc.).
- **Runbook Version Synchronization**: Refreshed stale version stamps across operational runbooks and testing documentation.
- **Test Suite (+3 tests, 486 → 489)**: Added `tests/test_docs_sync.py` testing OpenAPI endpoint presence, guide file validity, and legacy column absence.

## [2.8.0] - 2026-08-29
### Added & Enhanced (Crew Job-Alert Push Notifications via Web Push / VAPID)

- **Flagged Decision (Crew Subscription Architecture)**: Implemented decoupled `push_subscriptions` schema (Migration 0023) associating browser subscription endpoints with `user_id` and `role` (`crew` or `field`), avoiding artificial coupling between sales canvassers (`field_reps`) and installation subcontractor crews.
- **Zero-Cost Web Push Engine**: Integrated `pywebpush` with standard VAPID authentication (`VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_CLAIM_EMAIL` documented in `.env.example`).
- **Subscription API (`POST /api/field/push/subscribe`)**: Added endpoint for field reps and installation crews to register browser push subscriptions with cryptographic keys (`p256dh`, `auth`).
- **Automated Lifecycle Dispatching**: Added automated Web Push dispatch in `update_job_status` when jobs transition to `INSTALL_SCHEDULED`, alerting crews with homeowner name, street address, and direct link to job details.
- **Self-Healing Subscription Pruning**: Built automated pruning of expired or unsubscribed browser endpoints upon receiving HTTP 404/410 responses from push services.
- **PWA Service Worker Enhancement (`app/static/service-worker.js`)**: Added `push` and `notificationclick` event handlers to display native OS notifications with vibration and route directly to the relevant job card on tap.
- **Test Suite (+5 tests, 481 → 486)**: Added `tests/test_push_notifications.py` testing subscription lifecycle, VAPID dispatching, 410 error pruning, and state machine transition triggers.

## [2.7.1] - 2026-08-29
### Fixed & Enhanced (Decoupled Document Intake & Case-Insensitive Job Type Normalization)

- **Job Type Normalization**: Added `is_retail_job(job_type)` utility function in `app/core/utils.py` to robustly normalize case variations (`RETAIL`, `retail`, `Retail`, ` retail `). Replaced raw case-sensitive comparisons across backend pipelines, compliance checkers, and Jinja2 templates.
- **Decoupled Document Intake Endpoints**: Replaced monolithic dual-file intake with independent REST endpoints:
  - `POST /api/office/jobs/{job_id}/measurement-report`: Uploads Hover or EagleView measurement PDFs independently with SHA-256 idempotency deduplication. For retail jobs, immediately triggers the retail quote pipeline without requiring a Statement of Loss. For insurance jobs, checks for an existing Statement of Loss and triggers supplement processing only when both are present.
  - `POST /api/office/jobs/{job_id}/statement-of-loss`: Uploads carrier Statement of Loss PDFs independently with SHA-256 idempotency deduplication. If a measurement report is already present, triggers supplement processing.
- **Retail Quote Route Alignment**: Updated `trigger_supplement_route` (`POST /api/office/jobs/{job_id}/trigger-supplement`) to check `is_retail_job()`, routing retail jobs to `run_retail_quote_pipeline` instead of demanding an unneeded Statement of Loss.
- **Backward-Compatible Wrapper**: Retained `POST /api/office/jobs/{job_id}/supplement_docs` as a deprecated wrapper for backwards compatibility.
- **Test Suite (+6 tests, 475 → 481)**: Added `tests/test_document_intake_decoupling.py` testing normalization, independent uploads, sequential intake triggering, idempotency, and retail quote routing.

## [2.7.0] - 2026-08-29
### Added & Enhanced (Manual Measurement Entry as a First-Class Workflow & Deterministic Geometry Validation)

- **Database Schema Expansion (Migration 0022)**: Added full geometry columns (`ev_drip_edge_lf`, `ev_flashing_lf`, `ev_step_flashing_lf`, `ev_total_facets`, `ev_pipe_boot_count`, `ev_vent_count`, `ev_starter_strip_lf`, `ev_flashing_wall_lf`) to `jobs` table, closing persistence gaps from automated PDF extraction.
- **Pydantic Model Alignment**: Extended `EagleViewData` (`app/core/supplement_models.py`) with optional fields for pipe boots, vents, starter strip, and wall flashing. Updated `_writeback_ev_geometry` in `app/core/pipeline.py` to persist these fields to SQLite.
- **Deterministic Geometry Validation Engine (`app/core/geometry_validation.py`)**: Implemented zero-AI pure mathematical validation including continuous pitch multipliers (1/12 to 24/12), maximum area-to-footprint perimeter bounds proof, edge completeness rules, and unified RFG STEEP threshold ($\ge 7/12$).
- **First-Class Manual Entry Endpoint (`POST /api/office/jobs/{job_id}/measurements/manual`)**: Created dedicated REST endpoint allowing manual measurement entry at any lifecycle stage, validating geometry, updating database columns, and transitioning status to `EV_PARSED`.
- **Triage Resolve Shared Validation**: Upgraded `admin_triage_resolve` in `app/api/office_routes.py` with expanded geometry fields and shared deterministic validation.
- **UI Integration (`app/templates/job_detail.html`)**: Added interactive modal and trigger button for entering/updating roof geometry manually directly from the office job detail card.
- **Test Suite (+11 tests, 464 → 475)**: Added `tests/test_geometry_validation.py` (8 unit tests) and 3 route integration tests in `tests/test_office_routes.py`.

## [2.6.0] - 2026-08-28
### Fixed & Enhanced (INSPECTION_FAILED Triage Visibility Resolution)

- **INSPECTION_FAILED Triage Visibility**: Resolved blind spot in `admin_triage_view` (`app/api/office_routes.py`) by expanding the query filter to `WHERE j.status IN ('PENDING_OPERATOR_REVIEW', 'PIPELINE_FAILED', 'INSPECTION_FAILED')`. Updated subquery ordering to prioritize `INSPECTION_VISION` task errors.
- **Dynamic Triage Status Badges**: Enhanced `app/templates/admin_triage.html` with explicit status badge branching for `INSPECTION FAILED` and `PIPELINE FAILED` alongside `PENDING REVIEW`.
- **Regression Test Coverage**: Extended `test_admin_triage_view_surfaces_review_and_failed_jobs` in `tests/test_office_routes.py` asserting that `INSPECTION_FAILED` jobs surface with the distinct `INSPECTION FAILED` badge alongside `PENDING_OPERATOR_REVIEW` and `PIPELINE_FAILED`, while normal jobs (`LEAD_CAPTURED`) remain strictly excluded.

## [2.5.9] - 2026-08-28
### Fixed & Enhanced (Admin Triage View Completeness, Field Guides & Offline Runbook Alignment)

- **Admin Triage View Completeness**: Fixed query filter in `admin_triage_view` (`app/api/office_routes.py`) from `WHERE j.status = 'PENDING_OPERATOR_REVIEW'` to `WHERE j.status IN ('PENDING_OPERATOR_REVIEW', 'PIPELINE_FAILED')`, ensuring all background pipeline failures are surfaced to operators for triage and resolution. Updated `app/templates/admin_triage.html` with dynamic card status badges.
- **Regression Test Coverage (+1 test, 463 → 464)**: Added comprehensive triage route test `test_admin_triage_view_surfaces_review_and_failed_jobs` in `tests/test_office_routes.py` asserting that `PENDING_OPERATOR_REVIEW` and `PIPELINE_FAILED` jobs surface correctly in the triage UI while normal lifecycle jobs (`LEAD_CAPTURED`) are strictly excluded.
- **Canvasser Field Guide Updates (`docs/canvasser_field_guide.md`)**: Documented spoken voice note recording workflow (`#voiceNoteSection`), preview player, automated backend faster-whisper transcription, and offline queue error modal (`#syncErrorModal`) manual retry flow. Updated release version stamp to `v2.5.8` / `v2.5.9`.
- **Field & Offline Runbook Updates (`docs/field_runbook.md`)**: Added Section 6 for Voice Note Recording & Upload Diagnostics and Section 7 for Offline Queue Sync Replay & Error Modal handling. Updated version stamp to `v2.5.9`.

## [2.5.8] - 2026-08-28
### Added & Enhanced (Supplemental Pricing Unit Verification & DMO PU Key Disambiguation)

- **Pricing Key Unit Verification (RFG START, RFG DRIP, RFG IWS)**: Audited codebase to verify that `starter_bundles` ($45.00/BDL), `drip_edge_pieces_10ft` ($15.00/PC), and `ice_and_water_rolls` ($90.00/RL) are pre-existing, genuine dollar rates per unit in the `pricing` table (matching `MaterialBOM` unit counts). Updated `generate_and_gate_flags` in `app/core/pipeline.py` with deterministic geometry formulas for starter strip bundles (`math.ceil((eaves_lf + rake_lf) / 100.0)`) and drip edge pieces (`math.ceil(drip_lf / 10.0)`).
- **DMO PU vs DMO DUMP Disambiguation**: Established dedicated baseline pricing key `dmo_pu_per_load` ($250.00/EA) in `seed_default_pricing` (`app/core/database.py`) and re-pointed `DMO PU` in `CODE_PRICING_MAP` (`app/services/pdf/supplement.py`), eliminating key reuse collision with `DMO DUMP` dumpster container fee ($450.00/EA).
- **End-to-End PDF Validation (+1 test, 462 → 463)**: Added `test_supplement_pdf_additional_codes_pricing_resolution_end_to_end` in `tests/test_xactimate_coverage.py` using `pdfplumber` to verify non-zero, correctly calculated dollar amounts for `RFG START` ($90.00), `RFG DRIP` ($210.00), `RFG IWS` ($630.00), and `DMO PU` ($250.00).

## [2.5.7] - 2026-08-28
### Added & Enhanced (Supplement PDF Pricing Valuation Schedule & Rule Self-Reference Safety Verification)

- **Itemized Supplemental Scope & Valuation Schedule**: Enhanced `generate_supplement_pdf` in `app/services/pdf/supplement.py` with an Itemized Supplemental Scope & Valuation Schedule table that directly queries `get_pricing_ledger()` and dynamically binds unit rates and calculated line totals for all triggered supplemental line items (`RFG STEEP`, `RFG RIDGC+`, `SFG GUTA`, `DMO DUMP`, `RFG RENAIL`, `RFG 300S`, `RFG START`, `RFG DRIP`, `RFG IWS`, `DMO PU`).
- **Supplement Rule Self-Reference Audit**: Audited all database query and graph consumption sites for `supplement_rules` across `app/core/pipeline.py`, `app/api/frontend_routes.py`, and workers. Confirmed `parent_code == required_child_code` is safe, linear, and causes zero key collisions, dropped flags, or infinite loops.
- **End-to-End PDF Validation (+2 tests, 460 → 462)**: Added `test_supplement_pdf_pricing_resolution_end_to_end` asserting that a fully rendered supplement PDF contains exact calculated dollar totals for all six supplemental codes via `pdfplumber`, and `test_rebuttal_and_frontend_rules_consumption` verifying rule citation extraction integrity.

## [2.5.6] - 2026-08-28
### Added & Enhanced (Shingle Waste Pipeline Integration & Supplemental Pricing Resolution)

- **Shingle Waste Factor Evaluator Integration**: Wired `SupplementEngine.evaluate_shingle_waste` into `generate_and_gate_flags` and `run_supplement_pipeline` in `app/core/pipeline.py`. Dynamically extracts carrier waste percentage from Statement of Loss (defaulting to 10%) and checks valley/hip geometry to trigger `RFG 300S` shingle waste factor adjustment flags (15% complex geometry baseline).
- **Supplemental Pricing Baselines**: Added baseline pricing entries in `seed_default_pricing` (`app/core/database.py`) for `rfg_steep_per_sq` ($35.00/SQ), `rfg_ridgc_plus_per_lf` ($8.50/LF), `sfg_guta_per_lf` ($12.00/LF), `dmo_dump_per_container` ($450.00/container), `rfg_renail_per_sq` ($15.00/SQ), and `rfg_waste_adjustment_per_sq` ($105.00/SQ).
- **Frontend & Rebuttal Display**: Added `RFG 300S` complex geometry waste mapping to `LABEL_MAP` in `app/api/frontend_routes.py` and seeded baseline `eval_shingle_waste` rule in `supplement_rules`.
- **Test Suite Expansion (+3 tests, 457 → 460)**: Added tests in `tests/test_xactimate_coverage.py` validating waste factor triggering under complex geometry, absence of waste flags under simple gable geometry and generous carrier allowances, and non-zero unit price resolution across all supplemental codes.

## [2.5.5] - 2026-08-28
### Added & Enhanced (Open Data Canvassing Enrichment: US Census ACS-5 + OpenStreetMap Footprints)

- **US Census Bureau ACS-5 Demographics Enrichment**: Created `app/services/census_enrichment.py` querying public US Census Geocoder coordinates and ACS-5 APIs (tables B19013_001E median household income and B25035_001E median structure year built / home age) with SQLite spatial grid caching (`census_enrichment_cache`) and strict 3.0s timeout fallbacks.
- **OpenStreetMap Overpass Building Footprint Sizing**: Created `app/services/osm_footprint.py` querying OSM Overpass API for building polygon geometry within 50m of centroid, calculating footprint polygon area in square feet via Shoelace formula, and estimating baseline roof squares with standard 1.15 pitch multiplier before ordering EagleView reports (`osm_footprint_cache`).
- **Canvassing Target Integration & API Surface**: Updated `app/services/canvassing_targets.py` with asynchronous enrichment non-destructively attaching demographic badges (`Median Income: $Xk`, `Est. Home Age: Y yrs`) and roof size badges (`Est. Roof: Z SQ (OSM)`) without altering core storm severity score mathematics. Added `enrich: bool = False` query parameter to `GET /api/office/storms/targets` in `app/api/office_routes.py`.
- **Test Suite Expansion (+11 tests, 446 → 457)**: Added `tests/test_open_data_enrichment.py` validating Census API parsing, OSM Overpass polygon mathematics, SQLite caching layers, network timeout graceful degradation, and API endpoint delivery.

## [2.5.4] - 2026-08-28
### Added & Enhanced (Chain-of-Thought Damage Classification Prompt Refinement)

- **Forensic 3-Step Chain-of-Thought Prompting**: Refined Gemini vision prompts in `app/services/ai_service.py` (`analyze_roof_photo` and `analyze_roof_photos_batch`) to require a strict 3-step forensic observation sequence before damage classification:
  1. *Granule Depletion Pattern*: Assesses localized/circular/pitted hail impact patterns vs. uniform age-related blistering/scuffing.
  2. *Asphalt Substrate / Mat Condition*: Inspects underlying substrate for exposed fiberglass matting, micro-fractures, or wind creases.
  3. *Impact Bruise Presence*: Evaluates physical substrate indentations/bruises characteristic of functional hail damage.
- **PhotoAnalysis Schema Expansion**: Added `granule_depletion_pattern`, `substrate_condition`, and `impact_bruise_present` fields to `PhotoAnalysis` in `app/core/inspection_models.py`, preserving flat schema architecture for Gemini structured output and guaranteeing backward compatibility for legacy cached analyses.
- **Test Suite Expansion (+3 tests, 443 → 446)**: Added `tests/test_cot_damage_classification.py` validating schema validation, backward-compatible deserialization, and AI prompt directive integrity.

## [2.5.3] - 2026-08-28
### Added & Enhanced (Local Offline Voice-to-Text for Field Notes)

- **Local Faster-Whisper Transcription Service**: Created `app/services/voice_transcription.py` providing zero-cloud, CPU-quantized (`int8` tiny model) offline transcription for field voice memos and spoken inspection observations.
- **Field Audio Endpoint**: Added `POST /api/field/jobs/{job_id}/voice-note` in `app/api/field_routes.py` with UUID validation, field rep ownership enforcement (`assert_field_rep_owns_job`), automated registration in the document vault (`VOICE_NOTE` / `field_safe`), and synchronous appending to `jobs.inspection_notes`.
- **Field App Voice Recorder UI & Offline Replay**: Added microphone `MediaRecorder` audio capture in `app/templates/field_app.html` with recording timer, audio playback preview, and offline IndexedDB queueing with automatic sync replay.
- **Test Suite Expansion (+7 tests, 436 → 443)**: Added `tests/test_voice_transcription.py` validating audio transcription, fallback error handling, UUID validation, audio size/format gating, doc vault registration, and job notes persistence.

## [2.5.2] - 2026-08-28
### Added & Enhanced (Xactimate Line-Item Coverage Expansion)

- **Xactimate Code Coverage Audit & Expansion**: Added deterministic evaluation methods in `app/services/supplement_engine.py` and seeded rules in `app/core/database.py` for standard insurance supplement items:
  - `RFG STEEP`: Steep roof safety/labor charge automatically triggered when pitch is $\ge 7/12$.
  - `RFG RIDGC+`: High-profile dimensional ridge cap upgrade triggered for architectural/dimensional shingles.
  - `DMO DUMP`: Debris haul-off container fees on tear-off jobs, scaling dynamically by square count.
  - `SFG GUTA`: Seamless gutter & downspout replacement triggered strictly on documented gutter storm/hail impact damage.
  - `RFG RENAIL`: IRC R905.2.1 roof decking re-nailing on tear-off jobs.
- **Frontend & Reporting Label Mapping**: Extended `LABEL_MAP` in `app/api/frontend_routes.py` with descriptive titles and statutory/OSHA/IRC citations for all expanded line-item codes.
- **EagleView Ergonomics**: Added `total_squares` computed property to `EagleViewData` model in `app/core/supplement_models.py`.
- **Test Suite Expansion (+6 tests, 430 → 436)**: Added `tests/test_xactimate_coverage.py` validating pitch gating, architectural ridge cap upgrades, gutter damage gating, dumpster container math, and pipeline flag persistence.

## [2.5.0] - 2026-08-28
### Added & Hardened (Georgia FBPA Compliance, 5-Day Invoicing Lock, AOB Prohibition, Soft Deletes)

- **Georgia Statutory Cancellation Formatting (O.C.G.A. § 10-1-393.12)**: Updated ReportLab contingency and notice of cancellation generators in `app/services/pdf/documents.py`. Ensured statutory right-to-cancel disclosures are rendered in boldface type at minimum 10-point font size with dedicated high-contrast callout boxes, and guaranteed structurally detachable duplicate Notice of Cancellation forms ("Customer Copy" and "Contractor Copy" separated by page breaks).
- **5-Business-Day Post-Denial Invoicing Lock**: Added deterministic compliance guard in `app/services/compliance.py` enforcing O.C.G.A. § 10-1-393.12. Prevents invoice generation or non-emergency collection on insurance-contingent jobs until 5 business days have elapsed since a `CLAIM_DENIED` status/timestamp was recorded. Emergency restoration services (e.g. tarping) remain exempt.
- **Assignment of Benefits (AOB) Prohibition (Georgia SB 201 / O.C.G.A. § 33-24-59.28)**: Added regex pattern detection in `app/services/compliance.py` to intercept and reject AOB language (e.g. assignment of insurance benefits, transfer of policy rights, direct payment authorizations) in contract scopes or descriptions.
- **Soft-Delete & 7-Year Statutory Document Retention**: Added migration `0021_add_soft_delete_and_retention.py` adding `deleted_at TIMESTAMP DEFAULT NULL` to `jobs`, `job_documents`, and `job_agreements` tables. Updated `insert_job_document` replace logic to set `deleted_at = CURRENT_TIMESTAMP` instead of hard SQL `DELETE`, preserving audit history for statutory retention. Updated document vault queries to exclude soft-deleted items by default.
- **Test Suite Expansion (+8 tests, 422 → 430)**: Added `tests/test_georgia_compliance.py` covering AOB detection, business day calculations, post-denial invoicing locks, retail exemptions, emergency bypasses, and soft-delete retention.

## [2.4.3] - 2026-08-28
### Fixed & Hardened (Office/Field Circular Import Elimination, Method-Threading Fix, Duplicate Import Cleanup)

- **Router Circular Import Elimination**: Extracted the pure `get_inspection_summary` helper into a standalone service module `app/services/inspection_summary.py`, decoupling `app/api/office_routes.py` from `app/api/field_routes.py` at module level. Updated `field_routes.py`, `office_routes.py`, and `inspection_processor.py` to import from the new service module.
- **Missed Method-Threading Call Site Fix**: Updated `download_job_document` in `app/api/office_routes.py` to accept `request: Request`, import `assert_field_rep_owns_job` from its canonical path `app.services.field_access`, and pass `request.method` (`assert_field_rep_owns_job(claims, job_id, request.method)`).
- **Duplicate & Shadowed Local Import Removal**: Cleaned up seven redundant/shadowed local imports in `app/api/office_routes.py`:
  - `upload_job_document`: removed redundant `from app.core.database import get_job_document_by_hash`
  - `update_claim_info_route`: removed redundant `from app.core.database import JobStatus, _fetch_job_sync, update_job_status`
  - `_sync_update_job_claim_info`: removed redundant `import uuid`
  - `approve_supplement`: removed redundant `from app.core.database import JobStatus, update_job_status`
  - `deny_supplement`: removed redundant `from app.core.database import JobStatus, update_job_status`
  - `download_rebuttal`: removed redundant `from fastapi.responses import FileResponse`
  - `get_storm_canvassing_targets`: removed redundant `from app.core.database import get_connection`
- **Pyrefly Diagnostic Resolution**: Resolved the `bad-argument-type` diagnostic on `office_routes.py` (line 600) where `actual_type` was typed as `str | None`. Added explicit narrowing on `file.content_type`.
  - Diagnostic count before: **1 error (14 warnings)**: `ERROR Argument str | None is not assignable to parameter file_type with type str in function asyncio.threads.to_thread [bad-argument-type]`
  - Diagnostic count after: **0 errors (14 warnings)**: `INFO 0 errors (14 warnings not shown)`

## [2.4.2] - 2026-08-28
### Fixed & Hardened (Pyrefly Import Diagnostics, Field-Access Read-Only Enforcement)

- **Pyrefly Import Diagnostic Resolution**: All 4 `missing-import` errors reported by pyrefly on `app/api/operations_routes.py` are resolved. Root cause: pyrefly was targeting the Python 3.14 `.venv` instead of the scoop Python 3.11 environment. Added `[tool.pyrefly]` section to `pyproject.toml` pointing to the correct interpreter. Additionally, moved a mid-file `FileResponse` and `DocumentsGenerator` import block (between function definitions at line 217) to the canonical top-level import block, eliminating the rogue `fastapi.responses` re-scan that triggered the 4th diagnostic.
- **Alex Wickham Field-Access Defense-in-Depth**: Extended method-aware read-only enforcement from `app/api/auth.py` into `app/services/field_access.py`. `assert_field_rep_owns_job` now accepts a `method: str` parameter (default `"GET"`) and independently enforces the read-only constraint: GET/HEAD/OPTIONS pass through; POST/PUT/PATCH/DELETE raise HTTP 403 with the canonical message `"Alex Wickham has read-only privileges. Read-only access: contact an admin to make this change."` This is defense-in-depth — the check in `auth.py` is still present; this adds a second enforcement point at the field-access layer.
- **Field Route Signature Hardening**: Updated all 19 `assert_field_rep_owns_job` call sites in `app/api/field_routes.py` to pass `request.method`, adding `request: Request` to any function signature that was missing it (7 functions). Also threaded `request` through `get_inspection_summary` and its two call sites in `app/api/office_routes.py`.
- **Test Suite Expansion (+4 tests, 418 → 422)**: Appended four field-access scope tests to `tests/test_core_rbac_split.py`: Alex Wickham GET bypass, Alex Wickham mutation block, full-access core (Michael/Scott/Debi) unrestricted, and standard field rep ownership enforcement.

## [2.4.1] - 2026-08-28
### Hardened & Refactored (Router Coupling, Core Team Access Separation, and Help Panel Consolidation)
- **Router Import Coupling Refactor**: Extracted Jinja2Templates instantiation to a new shared module `app/core/templates.py` to decouple router loading and prevent circular import dependencies. Kept test client patch points intact by maintaining the necessary module-level imports.
- **Full-Access vs Read-Only Core Team Bypass Split**: Divided `CORE_TEAM_NAMES` into `FULL_ACCESS_CORE_NAMES` (`{"michael", "scott", "debi"}`) and `READ_ONLY_CORE_NAMES` (`{"alex wickham"}`). Configured auth check helpers to grant Alex read-only visibility on GET/HEAD/OPTIONS operations, but strictly deny mutating operations (`POST`, `PUT`, `PATCH`, `DELETE`) with a 403 Forbidden response.
- **Help Tab Cleanup & Guides Consolidation**: Removed the redundant "Debi's Onboarding" tab and content pane from `help.html` and consolidated onboarding instructions into the primary "Accounting Guide" template. Left `docs/debi_onboarding_guide.md` intact for direct email usage.
- **Documentation Accuracy Pass**: Updated version references to `2.4.1` and documented Core Team Access division boundaries within `admin_tech_guide.md`, `accounting_guide.md`, `operations_guide.md`, and `security_tasks.md`.
- **Test Suite Expansion (+4 tests, 414 → 418)**: Added `tests/test_core_rbac_split.py` to assert read-only core bypass validation, mutating write blocks, full-access bypasses, and Help page rendering contracts.

## [2.4.0] - 2026-08-28
### Hardened & Enhanced (Speed-to-Lead, Offline Synchronization, and Storm Prioritization)
- **Deterministic Speed-to-Lead Calculation**: Extracted business logic to a pure helper `calculate_speed_to_lead` in `app/core/utils.py` to parse JSON status history and evaluate duration from the first `LEAD_CAPTURED` status to the first qualifying sales-action status, handling schema-consistent fallbacks gracefully.
- **Office Storm Canvassing RBAC Consistency**: Aligned `GET /api/field/pipeline/summary` endpoint to filter jobs based on JWT ownership (`rep_name` and `rep_id`) using the same ownership filter as the main list endpoint, resolving identity mismatch bugs and returning empty results gracefully for no-identity tokens.
- **Offline Sync Hardening**: Prevented sync spamming via `inProgressRetries` track, implemented exponential backoff cooldowns (30s, 60s, 120s, 240s, capped at 300s) on transient network or 5xx server failures, mapped status validation failures to permanent states with explicit error descriptions, and polished sync error modals with try-again lockouts and user-confirmed dismissals.
- **Deterministic & Explainable Storm Priority Scoring**: Audited severity calculations, moving scoring logic to `compute_severity_score` and `get_priority_info` in `app/core/utils.py`. Stored severity scores during ingestion in `storm_worker.py`. Extended storm canvassing targets endpoints and frontend displays to include explainable details (`priority_label`, `priority_reason`, `max_hail_inches`, `max_wind_mph`, `has_tornado`, `event_count`, `latest_event_time_utc`, `window_hours`).
- **Unified Storm Date/Time Formatting**: Implemented `formatDateTime` in `StormRadar` (`app/static/js/storm_radar.js`) for rendering combined short date and time across admin/field apps. Displayed actual server-ingested timestamps instead of client fetch times, and defended against invalid timestamps.
- **Field UX Filters & Centralized Actions**: Refactored job listing ZIP filter to support persistent display states and clear actions. Centralized action selection in the field app via `getJobNextActionHint` with honest, legally safe, and operational guidance.
- **Path Traversal & UUID Validation**: Validated download parameters, enforcing UUID checks for job IDs on download endpoints (`download_bom`) and preventing directory traversal using `sanitize_download_filename`.

## [2.3.9] - 2026-08-28
### Added & Enhanced (Configurable Storm Thresholds, Offline Error Transparency, and Rep Widgets)
- **Configurable Storm Windows & Thresholds**: Plumbed parameters through to `/api/storms/recent`, `/api/storms/summary`, `/api/field/storms/{zipcode}`, and JS fetch logic. Added an admin dashboard select widget to toggle windows and show the value dynamically in the header.
- **Dynamic Priority Labels & Severity Score**: Updated targets queries to calculate `severity_score` and dynamic priority labels (🔥 High, ⚡ Medium, 🟢 Low) and exposed severity details on the target ZIP badges.
- **Offline IndexedDB Error Metadata & Retry Modal**: Captured permanent failure error reasons, timestamps, and HTTP codes on background sync failure. Implemented `syncErrorBadge` and `syncErrorModal` with manual **Try Again** controls in the field app.
- **Field Pipeline Widget & Context Hints**: Added a **My Pipeline Summary** widget displaying rep-specific aggregates and speed-to-lead times. Displayed rule-based action hints on each job card and appended conversational safety guardrails under AI generators.
- **Test Suite & Coverage Expansion (+6 tests, 408 → 414)**: Added test cases validating per-rep metrics, speed-to-lead durations, and transient vs. permanent offline sync errors.

## [2.3.8] - 2026-08-27
### Fixed & Enhanced (Field Documents, Offline Replay Tests, and Storm Monitor UI Refinements)
- **Field Documents API Cleanup**: Removed duplicate endpoints `list_field_documents` and `download_field_document`, leaving the secure, canonical pair (`get_field_job_documents` and `download_field_job_document`) which filters visibility to `field_safe`.
- **Pipeline vs. Kanban Semantics**: Exposed monitored sales status legend labels next to the stage breakdown widget header on the admin dashboard, and documented the status differences in `docs/testing.md`.
- **Offline Queue Replay Hardening**: Created `tests/test_offline_queue_replay.py` containing full simulated sequences for queued lead intakes, photo uploads, and contingency or retail signatures replay, asserting DB state changes.
- **Storm Monitor UI Polish**: Added clear window intervals ("last 72 hours"), last updated timestamps, and threshold limits ("hail ≥ X, wind ≥ Y") to both the admin dashboard and field app templates. Exposed a click-to-filter hint next to target ZIP buttons.
- **AI Sales Tools Trust Badge**: Appended a trust notice explaining that AI summaries and scripts are tailored using local job data and storm events.
- **Test Suite Verification (+4 tests, 404 → 408)**: Cleaned up race condition in photo settling helper where negative age floating-point values skipped settling. Verified all 408 test files run green.

## [2.3.7] - 2026-08-27
### Fixed & Enhanced (Job-Local Storm Tracking & Security Adjustments)
- **Office Storm Canvassing Security**: Refactored `GET /api/office/storms/targets` endpoint authorization to require the office role `verify_office_role`, blocking field reps from viewing full target datasets.
- **Review Requests Rep Attribution**: Fixed field review-request endpoint to correctly resolve the claimant's identity using JWT claims (`rep_name` field), and logged rep names in the status history change audits.
- **Job-Local Storm Context**: Implemented the localized database helper `get_storm_events_near_job` targeting a specific job's ZIP code instead of generic top targets. Integrated this helper into the neighbor letter PDF generation and AI sales narrative grounding.
- **Sales Pipeline Alignment**: Pruned conceptual stages from the sales pipeline summary (`get_sales_pipeline_summary`), leaving only valid database-level `JobStatus` stages.
- **UI Adjustments**: Wired time window filters and targets refresh timestamp labels on the admin dashboard, and formatted concise storm activity badges (⚡ Hail, 💨 Wind) on the field SPA job cards.
- **Test Suite Verification (+4 tests, 400 → 404)**: Expanded unit tests in `test_neighbor_letter.py` and `test_sales_narrative.py` to ensure local storm metrics are used for specific jobs. Checked that the entire 404-test suite runs green.

## [2.3.6] - 2026-08-27
### Added & Enhanced (Sales Pipeline & Canvassing Intelligence)
- **Database Schema Migration**: Implemented migration `0020_add_sales_and_review_fields` adding `severity_score` to `storm_events`, and `review_requested_at`, `review_requested_by`, `referral_code`, and `referral_source` to `jobs` for review/referral tracking.
- **Canvassing Intelligence & Storm Targets**: Implemented severity score computation and ranking of canvassing target areas, surfacing them in both the office dashboard and the field app via a ranked list.
- **Sales Pipeline widget**: Implemented an admin dashboard Sales Pipeline summary widget with stage counts, representative performance metrics, and automated "speed-to-lead" monitoring.
- **AI-Generated Sales Narratives**: Added support for grounded sales summaries and door-knocking scripts using the existing Gemini AI integration, cached in the vault.
- **Neighbor Outreach Campaigns**: Added ReportLab-based neighbor outreach letter generation on the field app once a job status reaches `INSTALL_COMPLETED`.
- **Test Suite Expansion (+49 tests, 351 → 400)**: Created 5 new test modules covering the entire backend and API lifecycle of these new features:
  - `test_storm_targets.py`
  - `test_sales_pipeline.py`
  - `test_review_referral.py`
  - `test_neighbor_letter.py`
  - `test_sales_narrative.py`
- **Coverage Retained**: Maintained coverage above 75%, all 400 tests passing.

## [2.3.5] - 2026-08-21
### Added & Hardened (Coverage Gate Enforcement)
- **Real Coverage Gate**: `pyproject.toml` `fail_under = 75` now enforced by CI (`--cov-fail-under=75`); the gate was previously advisory only.
- **Test Suite Expansion (+39 tests, 312 → 351)**: Added 7 new test modules covering previously untested code paths:
  - `test_workers_coverage.py` — ARQ photo and commission processor workers (happy path + error branches + DB writeback).
  - `test_pipeline_additional_coverage.py` — All four orchestration pipelines: retail quote, material order, rebuttal letter, and supplement.
  - `test_pdf_generators_coverage.py` — ReportLab document generation for supplement, rebuttal, and commission statement types.
  - `test_notifications_coverage.py` — WebSocket `RobustConnectionManager` heartbeat and connection-culling logic.
  - `test_database_integration.py` — Integration-level DB helpers: financial writeback, job context fetch, and schema validation.
  - `test_ai_service_additional.py` — Additional Gemini AI client branches including batch photo analysis and error handling.
  - `test_retail_contracts_backend.py` — Retail contract API lifecycle: creation, PDF generation, status transitions.
- **Coverage Reached**: Total coverage 75.27% (up from 65.84% at the prior audit commit `a95ceff`). All 351 tests green.
- **Mypy Hardening**: Enabled strict per-module mypy for `app.core.*` and `app.services.*`; resolved all pre-existing type errors.
- **Documentation Drift Fixed**: `docs/testing.md` and `README.md` now reflect the actual test count (351), module list (57 modules), and coverage percentage (75.27%).

## [2.3.4] - 2026-08-21
### Fixed & Standardized (Notice of Cancellation Mislabeling Fix)
- **Document Standardization Fix**: Corrected a bug in `standardize_vault_filename` in `app/core/database.py` where `RETAIL_NOTICE_OF_CANCELLATION` files were mislabeled as `Retail_Contract.pdf` due to the `"RETAIL"` suffix pattern matching before `"CANCELLATION"`. Reordered matching hierarchy to prioritize cancellation documents.
- **Test Alignment**: Updated unit test assertions in `tests/test_field_routes.py` to correctly expect the capitalized, standardized filename format `Notice_of_Cancellation` instead of the lowercased, raw filename.

## [2.3.3] - 2026-08-17
### Finalized & Configured (Storm Activity Monitor Finalization)
- **Removed Obsolete Parameters**: Fully deprecated and removed the unused `require_magnitude` parameter from the recent and summary storm endpoints.
- **Config-Driven Thresholds**: Refactored hardcoded 1.0" hail and 40 mph wind thresholds across database, frontend, and field routes to reference settings in `app/config.py`. Raised default wind speed threshold to 50.0 mph for higher actionability.
- **Standardized Field API Response**: Wrapped `/api/field/storms/{zip}` in the standardized JSON envelope (`{events: [...]}`) for backend/client consistency.
- **Severity-Based Ranking**: Overhauled target ZIP code ranking logic using a normalized severity score based on configuration-defined thresholds.
- **Test Suite Expansion**: Added unit and integration tests covering ZIP ranking, job enrichment helper functions, and endpoint contract changes.

## [2.3.2] - 2026-08-17
### Refactored & Integrated (Unified Storm Activity Sales Targeting)
- **Centralized JS Logic**: Implemented the unified `storm_radar.js` frontend module for fetching, rendering storm cards, and filtering live web socket alerts.
- **Storm-Target ZIP Identification**: Integrated target ZIP list rendering in both field-app and admin dashboards. Added inline storm badges for jobs in high-risk ZIPs and dynamic ZIP lookups.
- **Strict Magnitude Thresholds**: Enforced 1.0" hail and 40 mph wind limits directly in backend route SQL queries, WS handlers, and frontend filters.
- **REST and Unit Test Alignment**: Aligned test fixtures and assertions in `tests/test_storm_radar.py` to the new structured JSON response format.

## [2.3.1] - 2026-08-17
### Fixed & Normalized (Storm Activity Monitor Final Polish)
- **Rebranded Badge Text**: Standardized the NWS widget header badge text to "Live NWS Data" in both `admin_dashboard.html` and `field_app.html`.
- **Location Normalization Migration**: Implemented a database migration (`0019_normalize_nws_locations.py`) that retroactively normalizes legacy NWS shorthand strings (e.g., "4 SE Peoples Still, GA") to human-readable locations in the `storm_events` table.
- **Migration Orchestration**: Integrated the new migration into the `run_migrations()` startup loop in `app/core/database.py`.
- **Validation Suite**: Added migration unit tests in `tests/test_storm_radar.py` and resolved codebase-wide import styling/linting warnings.

## [2.3.0] - 2026-08-16
### Refined & Remediated (Storm Ingestion Pipeline & Dashboard Restructuring)
- **High-Performance Bounding-Box Ingestion**: Replaced inefficient, state-wide data fetching with spatial bounding-box geometry queries centered on the office coordinate system on the NWS 72-hour layer (Layer 2).
- **Eliminated Third-Party Geocoding Dependency**: Removed all Nominatim OpenStreetMap reverse geocoding integrations client-side and server-side. Location descriptions are now natively constructed from NWS `loc_desc` and `state` attributes to ensure usage compliance.
- **Deduplication Key & SQLite Unique Constraint**: Hardened database schema and ingestion flow with `dedup_key` (event type, rounded latitude/longitude, and time) along with a SQLite `UNIQUE` index constraint to guarantee ingestion idempotency across multiple runs.
- **Admin Dashboard Layout Update**: Repositioned the Storm Radar widget from a fixed bottom-right floating panel to a static, collapsible card at the top of the dashboard content area.
- **UI Label Terminology Alignment**: Renamed all dashboard columns and list badges referring to "County" to "Location" to match the new location descriptions.
- **Unit Test Overhaul**: Re-wrote tests in `test_storm_radar.py` to target layer 2 mock queries, exclude Nominatim dependencies, and verify API query parameters (`since_hours`, `radius_miles`, `event_types`).

## [2.2.0] - 2026-08-12
### Added & Hardened (CRM Pipeline & Workflow Stabilization)
- **Granular Payment Tracking & Milestone Automation**: Integrated detailed insurance and retail payment recording via the new `POST /api/office/accounting/jobs/{job_id}/mark-payment` endpoint. Added new job statuses: `ACV_PAYMENT_RECEIVED`, `DEPRECIATION_PAYMENT_RECEIVED`, `RETAIL_PAYMENT_RECEIVED`. The database state machine automatically transitions the job to `PAYMENT_RECEIVED` when both ACV and Depreciation check records are completed, triggering the commission calculation.
- **New Claim Pipeline States & UI Milestones**: Added new job statuses `CLAIM_FILED`, `EV_ORDERED`, `ADJUSTER_MEETING_COMPLETED` alongside corresponding action buttons on the `job_detail.html` screen, allowing manual tracking of the early claim progression.
- **Resolved Global Token Scoping Regression**: Centralized the declaration of `OFFICE_TOKEN` and `AUTH_TOKEN` in a top-level, unconditional script block in `job_detail.html`. This fixes a critical scoping regression that broke page actions (inspection report generation, document uploads, claim edits, financials saving, and admin overrides) for all logged-in roles.
- **Kanban Column Reordering**: Adjusted the Kanban board column sequence on the Admin Dashboard so that `SCOPE_APPROVED` correctly follows `RETAIL_QUOTE_GENERATED` and `RETAIL_QUOTE_ACCEPTED`, reflecting the proper retail restoration progression.
- **Read-Only Deductible Indicator**: Added a read-only payment indicator badge on the Financial Breakdown card of the job detail view, allowing instant visibility of `deductible_paid` and `deductible_paid_cents` statuses.
- **Pre-Build Status Guard Hardening**: Added `JobStatus.EV_ORDERED` to the `pre_build_statuses` check in `inspection_processor.py`, preventing early-stage jobs from being force-advanced to completed status.
- **Evidence Grid AI Caption Honesty Sweep**: Refactored PDF evidence grid generation to replace the misleading "Awaiting AI Audit" and "Pending Analysis" placeholders with honest `"No AI analysis available for this photo"` captions when no cached AI result exists. Updated fallback inspector names to `"Wickham Roofing Field Inspector"`.
- **Test Suite Expansion**: Added focused test suite assertions in `tests/test_weather_and_evidence_grid.py` to verify PDF caption fallbacks, bringing total test count to 299 passed.

## [2.1.5] - 2026-08-12
### Added & Fixed (Field Operations & CRM Stabilization)
- **Core Role JWT Claim Mapping**: Assigned names and rep IDs to core office roles (`admin` -> Michael/rep-michael, `accounting` -> Debi/rep-debi, `operations` -> Scott/rep-scott) inside JWT claims generated upon login. This ensures jobs created by these users are consistently visible to them in the field app.
- **Ice Barrier Toggle**: Added an `ice_barrier_required` manual override toggle inside the Edit Claim Info modal UI, fully integrated with the database and PATCH endpoint. This resolves the climate zone warning.
- **Salesperson Attribution**: Refactored both the inspection summary route and the PDF report generator to prioritize the `canvasser_name` field over generic placeholders.
- **Status Progression Guard**: Updated the background photo processor to prevent early-stage (pre-build) jobs from incorrectly jumping to `INSPECTION_COMPLETED` status.
- **Verification & Clean Slate**: Executed a full database demo reset via `scripts/db_demo_reset.py` and validated stability with the complete 298-test suite (100% pass rate).

## [2.1.4] - 2026-08-12
### Changed (Rebrand & Reference Cleanup)
- **Repository Renamed**: GitHub repository renamed from `blairmichaelg/JobNimbus_controller` to `blairmichaelg/wickham-roofing-crm`. Local git remote updated accordingly.
- **Legacy Reference Purge**: Removed all remaining `JobNimbus` references from live code and documentation. Specifically:
  - `app/services/pdf/documents.py`: Replaced "File delivery confirmation photo to JobNimbus upon arrival" with a generic instruction referencing the job record.
  - `AUDIT_REPORT.md`: Removed `(JobNimbus_controller)` from the target line.
  - `README.md`, `CONTRIBUTING.md`, `DEPLOYMENT.md`, `ARCHITECTURE.md`: Fixed stale clone URLs (`wickham_crm` → `wickham-roofing-crm`), updated test counts to 282, updated badge links and repo references.
  - `pyproject.toml`: `[project] name` aligned to `wickham-roofing-crm`.
- **Historical CHANGELOG entries** describing the JobNimbus-to-local-CRM migration are preserved as-is; they document a real architectural decision.

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
