# Testing Guide — Wickham Roofing CRM

This guide documents the **72 test modules** (491 assertions) comprising the Wickham Roofing CRM test suite, mapping each module to the business guarantee it protects, documenting how to run the suite with coverage, and specifying the enforced threshold targets.

---

## Running the Full Suite

```powershell
# Run all tests (fast, no coverage)
.\venv\Scripts\python.exe -m pytest tests/ -v

# Run with coverage (required before opening a PR)
.\venv\Scripts\python.exe -m pytest tests/ -v --cov=app --cov-report=term-missing

# Run with HTML coverage report
.\venv\Scripts\python.exe -m pytest tests/ --cov=app --cov-report=html
# Then open: htmlcov/index.html
```

---

## Coverage Thresholds

| Package | Required Coverage |
|---|---|
| `app/core/` | ≥ 90% |
| `app/services/` | ≥ 90% |
| Rest of `app/` | ≥ 75% |
| Overall | ≥ 75% (enforced via `fail_under = 75` in `pyproject.toml`; current: **76.79%**) |

---

## Test Module Map

| Module | Business Guarantee Protected |
|---|---|
| `test_ai_safety_and_math.py` | AI prompts contain mandatory `CRITICAL NO-MATH DIRECTIVE`; Pydantic schema rejects hallucinated arithmetic on claim ASTs |
| `test_ai_service.py` | Gemini photo analysis service returns validated `PhotoAnalysis` objects; multimodal batching and fallback paths work correctly |
| `test_cache.py` | SQLite cache layer (`app/core/cache.py`) initialises cleanly, stores, retrieves, and evicts analysis records |
| `test_cot_damage_classification.py` | 3-step forensic Chain-of-Thought damage observation sequence, flat schema validation, and backward-compatible deserialization |
| `test_open_data_enrichment.py` | US Census ACS-5 income/age enrichment, OpenStreetMap Overpass building footprint area calculation/roof square estimation, SQLite caching, and timeout resilience |
| `test_georgia_compliance.py` | Georgia statutory FBPA cancellation notice formatting (O.C.G.A. § 10-1-393.12), 5-business-day post-denial invoicing lock, AOB prohibition (SB 201), and 7-year retention soft-deletes |
| `test_voice_transcription.py` | Local offline voice note transcription via faster-whisper (CPU/int8), document vault registration, and job note appending |
| `test_xactimate_coverage.py` | Xactimate line-item coverage rules: steep roof pitch charges, high-profile ridge cap upgrades, documented gutter damage gating, dumpster haul-off scaling, and sheathing re-nailing |
| `test_cleanup.py` | Temp-file cleanup manager unlinks orphaned uploads without touching legitimate job files |
| `test_climate_gate.py` | Climate gating rules enforce Ice & Water Shield mandates for northern zip codes and skip them for Georgia |
| `test_code_router.py` | Smart Code RAG parser correctly indexes building-code text files and returns relevant citations for supplement discrepancies |
| `test_commissions_coverage.py` | Commission calculation engine correctly computes rep earnings, respects manual overrides, and handles edge cases |
| `test_complexity.py` | Roof complexity scoring produces deterministic outputs for standard pitch/facet combinations |
| `test_core_rbac_split.py` | Core team bypass logic validation: full-access core (Michael, Scott, Debi) vs read-only core (Alex Wickham) mutating blocks |
| `test_cron_storm_ingest.py` | Storm ingest cron job correctly fetches and persists NOAA weather events |
| `test_database.py` | SQLite WAL migrations run idempotently; core CRUD operations and backup/restore maintain data fidelity |
| `test_document_parser.py` | Statement-of-Loss PDF parser extracts structured claim data and rejects malformed documents |
| `test_e2e_pipeline.py` | Full job lifecycle: lead intake → EagleView upload → supplement generation → QBO export → status transition |
| `test_field_routes.py` | Field API endpoints enforce IDOR boundaries, reject cross-rep access, and return correct job/document data |
| `test_happy_path.py` | Canonical new-lead → signed contingency → supplement pipeline executes without errors |
| `test_hover_extractor.py` | Hover measurement PDF extractor correctly parses area, linear footage, and facet data |
| `test_hover_integration.py` | End-to-end Hover report ingestion pipeline reaches correct job status |
| `test_ingestion.py` | Universal Claim AST validates carrier RCV/ACV math and SHA256 provenance; rejects internal inconsistencies |
| `test_inspection_engine.py` | Inspection processor correctly batches photos, handles AI fallbacks, and populates evidence grid |
| `test_inspection_models.py` | `PhotoAnalysis` Pydantic model enforces confidence bounds and damage classification constraints |
| `test_job_costing.py` | Job costing engine correctly computes margin, overhead, labor, and commission from raw financial inputs |
| `test_office_routes.py` | Office API endpoints (EagleView upload, supplement trigger, financials, production) return correct responses and status codes |
| `test_offline_queue_replay.py` | Offline queue IndexedDB replay operations, photo uploads, signature syncing, 4xx/5xx HTTP errors handling and permanent failure transitions |
| `test_pdf_extractor.py` | PDF text extraction utilities correctly parse EagleView and SoL report formats |
| `test_pdf_generator.py` | ReportLab PDF generation produces files for all 10 document types without errors |
| `test_phase4_hardening.py` | Rate limiter rejects flood requests; path traversal sanitizer blocks directory escape attempts |
| `test_phase5_surfaces.py` | Roof surface calculation methods handle hips, ridges, valleys, and complex geometry correctly |
| `test_phase6_state_machine.py` | State machine transitions are valid from each source status; invalid transitions are rejected |
| `test_phase7.py` | `days_since` utility and STATUS_LABELS dict return correct human-readable values |
| `test_phase8.py` | ARQ worker task enqueueing, deduplication, and error-path flag insertion work correctly |
| `test_phase9.py` | Dynamic field rep PIN authentication, IDOR enforcement, and commission statement scoping |
| `test_property_supplement.py` | Property-based (Hypothesis) tests for SupplementEngine math: non-negative quantities, waste ≥ 100%, state machine reachability |
| `test_qbo_export.py` | QBO CSV export contains correct line items, amounts, and customer data for all job types |
| `test_rbac_hardening.py` | Role-based access control prevents cross-role data access across admin/operations/accounting/field endpoints |
| `test_sales_pipeline.py` | Sales pipeline widget aggregates, rep metrics, speed-to-lead calculations, and field rep specific pipeline summary endpoint |
| `test_reconciliation.py` | Financial reconciliation view correctly aggregates revenue, costs, margin, and deductible across multiple jobs |
| `test_security_phase3_task1.py` | JWT tokens are rejected when algorithm is `none` or signature is invalid |
| `test_security_phase3_task2.py` | Admin PIN is required for admin-only endpoints; accounting/operations PINs are correctly scoped |
| `test_security_phase3_task3.py` | Rate limiter correctly blocks burst requests on protected endpoints |
| `test_security_phase3_task4.py` | Document download endpoints sanitize filenames and reject path traversal payloads |
| `test_security_phase3_task6.py` | WebSocket connections without a valid auth token are rejected immediately |
| `test_shingle_endpoints.py` | Shingle type/color metadata endpoints store and retrieve data correctly for all job states |
| `test_state_machine.py` | Core state machine transition table covers all valid paths with no missing transitions |
| `test_storm_radar.py` | NOAA/NWS storm radar ingestion, bounding box calculations, API parameter filtering, and live websocket alert broadcast validation |
| `test_storm_targets.py` | Storm canvassing targets and severity score prioritisation calculations |
| `test_supplement_engine.py` | Pure math kernel produces correct material quantities for standard and edge-case roof measurements |
| `test_supplement_models.py` | Supplement Pydantic schemas validate material BOM, discrepancy lists, and code citations |
| `test_ui_contracts.py` | HTML template rendering produces expected element structure for all dashboard pages |
| `test_upload_utils.py` | File upload sanitization correctly restricts extensions, enforces size limits, and names files deterministically |
| `test_weather_and_evidence_grid.py` | NOAA forensics engine retrieves storm events and evidence grid PDF generation includes correct weather data |
| `test_webhooks.py` | Webhook endpoints correctly validate HMAC signatures and process job status update payloads |
| `test_worker_settings.py` | ARQ worker settings module resolves Redis connection string from environment variables |
| `test_workers_coverage.py` | ARQ background workers (photo_processor, commission_processor) cover happy-path, error-path, and DB writeback branches |
| `test_pipeline_additional_coverage.py` | Orchestration pipelines (retail quote, material order, rebuttal letter, supplement) cover success and edge-case branches |
| `test_pdf_generators_coverage.py` | PDF generation helpers cover all document types including supplement, rebuttal, and commission statement outputs |
| `test_notifications_coverage.py` | WebSocket RobustConnectionManager heartbeat loop and connection culling logic |
| `test_database_integration.py` | Integration-level tests for core database helpers including financial writeback and job context fetch |
| `test_ai_service_additional.py` | Additional Gemini AI client branches: batch photo analysis, SOL extraction, and error handling |
| `test_retail_contracts_backend.py` | Retail contract API endpoints: creation, PDF generation, status transitions, and validation |

---

## Adding a New Test

1. Create `tests/test_<feature>.py`
2. Import `app` from `app.main` (re-exported from `app.server`) and use `TestClient`
3. Add an entry to the **Test Module Map** above
4. All tests must pass before opening a PR: `pytest tests/ -v`

---

## Adding a New Building-Code Rule

1. Edit the relevant file in `building_codes/` (e.g., `irc_2024.txt`) using the XML tag format: `<SECTION_R905_X_X>...text...</SECTION_R905_X_X>`
2. Add the keyword-to-tag mapping in `app/core/code_router.py` under `KEYWORD_MAP`
3. Add a test assertion in `tests/test_code_router.py`

## Adding a New Commission Scheme

1. Edit `app/core/job_costing.py` — add a new calculation branch
2. Update `tests/test_commissions_coverage.py` with the new scenario
3. Ensure the DB schema in `app/core/database.py` supports any new fields via a migration


## Sales Pipeline Widget vs. Kanban Columns Semantics

The Wickham Roofing CRM exposes two distinct layouts for tracking job statuses: the **Kanban Board** and the **Sales Pipeline Summary Widget**.

### 1. The Visual Kanban Board
The Kanban board on the Admin Dashboard lists all jobs categorized across the full range of active columns. This includes every single state represented in `JobStatus` (including intermediate processing, photo uploads, technical error pipeline states, and retail specific stages).

### 2. The Sales Pipeline Widget
The Sales Pipeline widget at the top of the dashboard displays aggregated counts only for **monitored sales stages**. This represents the primary milestones in the customer-facing pipeline, defined by `SALES_STAGES` in `app/core/database.py`.

### Status Mapping and Coverage
The pipeline widget tracks the following subset of status keys:
* `LEAD_CAPTURED`
* `CONTINGENCY_SIGNED`
* `CLAIM_FILED`
* `RETAIL_CONTRACT_SIGNED`
* `ADJUSTER_MEETING_COMPLETED`
* `SUPPLEMENT_GENERATED`
* `SUPPLEMENT_APPROVED`
* `SCOPE_APPROVED`
* `INSTALL_COMPLETED`
* `INVOICED`
* `CLOSED`

Conceptual, technical processing, or back-end only error stages (such as `PHOTOS_UPLOADED`, `EV_ORDERED`, `PENDING_OPERATOR_REVIEW`, `INSPECTION_FAILED`, `PIPELINE_FAILED`) are **excluded** from the sales pipeline summary to keep the metrics action-oriented and clean. Consequently, total counts in the pipeline widget and the active column totals in the Kanban view will differ.

