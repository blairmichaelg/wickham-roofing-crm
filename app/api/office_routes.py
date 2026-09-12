"""
app/api/office_routes.py — Backward-compatibility re-export shim.
Decomposed into domain modules under app/api/office/:
  - app/api/office/billing.py
  - app/api/office/scheduling.py
  - app/api/office/contracts.py
  - app/api/office/jobs.py
  - app/api/office/router.py
"""

from fastapi import BackgroundTasks

from app.api.office.billing import (
    AccountingBrief,
    CommissionOverridePayload,
    FinancialsPayload,
    MarkPaymentPayload,
    TogglePaymentPayload,
    _sync_update_job_financials,
    commission_override_route,
    create_invoice_route,
    download_commission,
    download_qbo_export,
    export_qbo_csv,
    get_accounting_brief,
    get_commissions_ready,
    mark_commission_paid,
    mark_payment_route,
    toggle_payment_route,
    update_job_financials,
)
from app.api.office.contracts import (
    EXPORT_DIR,
    _fetch_homeowner_name_sync,
    approve_supplement,
    deny_supplement,
    detect_pdf_format,
    download_cancellation,
    download_completion,
    download_contingency,
    download_escalation,
    download_evidence_grid,
    download_export,
    download_job_document,
    download_po,
    download_rebuttal,
    download_supplement_pdf_route,
    get_inspection_letter,
    mark_supplement_sent_route,
    queue_escalation,
    run_supplement_pipeline,
    trigger_supplement_route,
    upload_eagleview,
    upload_job_document,
    upload_measurement_report,
    upload_statement_of_loss,
    upload_supplement_docs,
)
from app.api.office.jobs import (
    JobClaimInfoPayload,
    ManualMeasurementPayload,
    ReferralPayload,
    ReviewRequestPayload,
    ShingleInfoPayload,
    _sync_update_job_claim_info,
    admin_triage_resolve,
    admin_triage_view,
    backup_database,
    get_all_jobs,
    get_job_details,
    get_jobs_sanity_check,
    get_pipeline_summary,
    manual_measurement_entry,
    office_add_referral,
    office_request_review,
    reassign_canvasser,
    update_claim_info_route,
    update_shingle_info_route,
    validate_geometry_dict,
)
from app.api.office.router import router
from app.api.office.scheduling import (
    ManualFlashingPayload,
    MaterialOrderPayload,
    MaterialRow,
    OperationsBrief,
    ProductionPayload,
    _sync_update_job_production,
    generate_material_order,
    get_operations_brief,
    get_storm_canvassing_targets,
    manual_flashing,
    update_job_production,
)
from app.core.pipeline import run_full_office_pipeline
