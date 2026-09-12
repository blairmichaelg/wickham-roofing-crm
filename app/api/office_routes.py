"""
app/api/office_routes.py — Backward-compatibility re-export shim.
Decomposed into domain modules under app/api/office/:
  - app/api/office/billing.py
  - app/api/office/scheduling.py
  - app/api/office/contracts.py
  - app/api/office/jobs.py
  - app/api/office/router.py
"""

from app.api.office.router import router

from app.api.office.billing import (
    FinancialsPayload,
    AccountingBrief,
    TogglePaymentPayload,
    MarkPaymentPayload,
    CommissionOverridePayload,
    download_qbo_export,
    _sync_update_job_financials,
    update_job_financials,
    get_accounting_brief,
    export_qbo_csv,
    get_commissions_ready,
    download_commission,
    toggle_payment_route,
    mark_payment_route,
    commission_override_route,
    create_invoice_route,
    mark_commission_paid,
)

from app.api.office.scheduling import (
    ProductionPayload,
    ManualFlashingPayload,
    MaterialOrderPayload,
    MaterialRow,
    OperationsBrief,
    _sync_update_job_production,
    update_job_production,
    generate_material_order,
    manual_flashing,
    get_operations_brief,
    get_storm_canvassing_targets,
)

from app.api.office.contracts import (
    EXPORT_DIR,
    _fetch_homeowner_name_sync,
    upload_eagleview,
    upload_measurement_report,
    upload_statement_of_loss,
    upload_supplement_docs,
    download_evidence_grid,
    download_job_document,
    download_export,
    upload_job_document,
    get_inspection_letter,
    download_po,
    download_cancellation,
    download_completion,
    download_contingency,
    trigger_supplement_route,
    mark_supplement_sent_route,
    download_supplement_pdf_route,
    approve_supplement,
    deny_supplement,
    download_rebuttal,
    queue_escalation,
    download_escalation,
    detect_pdf_format,
    run_supplement_pipeline,
)
from app.core.pipeline import run_full_office_pipeline

from app.api.office.jobs import (
    ShingleInfoPayload,
    JobClaimInfoPayload,
    ManualMeasurementPayload,
    ReviewRequestPayload,
    ReferralPayload,
    get_all_jobs,
    get_jobs_sanity_check,
    get_job_details,
    update_claim_info_route,
    update_shingle_info_route,
    _sync_update_job_claim_info,
    admin_triage_view,
    validate_geometry_dict,
    manual_measurement_entry,
    admin_triage_resolve,
    reassign_canvasser,
    get_pipeline_summary,
    office_request_review,
    office_add_referral,
    backup_database,
)

from fastapi import BackgroundTasks
