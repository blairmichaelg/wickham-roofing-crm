"""
app/core/status_labels.py — Human-readable labels for CRM job statuses.

This is pure domain data with no I/O or framework dependencies.
Consumed by Jinja2 template filters (registered in app/server.py) and
any future serialization layers.
"""

STATUS_LABELS: dict[str, str] = {
    "LEAD_CAPTURED": "New Lead",
    "CONTINGENCY_SIGNED": "Agreement Signed",
    "RETAIL_CONTRACT_SIGNED": "Retail Contract Signed",
    "CLAIM_FILED": "Claim Filed — Waiting on Adjuster",
    "ADJUSTER_MEETING_COMPLETED": "Adjuster Met — Waiting on Estimate",
    "PHOTOS_UPLOADED": "Photos Uploaded",
    "EV_PARSED": "Measurements Received",
    "MEASUREMENT_PARSED": "Measurements Received",
    "STATEMENT_OF_LOSS_RECEIVED": "Insurance Estimate Received",
    "PENDING_OPERATOR_REVIEW": "Manual Review Required",
    "PIPELINE_FAILED": "Processing Error — Needs Attention",
    "INSPECTION_FAILED": "Inspection Processing Failed",
    "SUPPLEMENT_GENERATED": "Supplement Ready to Send",
    "SUPPLEMENT_SUBMITTED": "Supplement Sent to Carrier",
    "SUPPLEMENT_DENIED": "Supplement Denied — Needs Rebuttal",
    "SUPPLEMENT_APPROVED": "Supplement Approved",
    "SCOPE_APPROVED": "Scope Approved",
    "MATERIAL_ORDERED": "Materials Ordered",
    "MATERIALS_ON_SITE": "Materials On Site",
    "INSTALL_SCHEDULED": "Install Scheduled",
    "INSTALL_COMPLETED": "Install Completed",
    "INSPECTION_COMPLETED": "Initial Inspection Completed",
    "FINAL_INSPECTION": "Final Inspection",
    "FINAL_INSPECTION_COMPLETED": "Final Inspection Completed",
    "INVOICED": "Invoiced",
    "PAYMENT_RECEIVED": "Payment Received",
    "CLOSED": "Job Closed",
    "RETAIL_QUOTE_GENERATED": "Quote Generated",
    "RETAIL_QUOTE_ACCEPTED": "Quote Accepted",
    "RETAIL_QUOTE_DECLINED": "Quote Declined",
    "AWAITING_CARRIER_RESPONSE": "Waiting on Insurance Company",
    "APPRAISAL_INVOKED": "Appraisal Process Started",
    "CLAIM_DENIED": "Claim Denied by Insurer",
}
