"""
app/api/field_routes.py — Backward-compatibility re-export shim.
Decomposed into domain modules under app/api/field/:
  - app/api/field/leads.py
  - app/api/field/photos.py
  - app/api/field/signatures.py
  - app/api/field/documents.py
  - app/api/field/sales_tools.py
  - app/api/field/radar.py
  - app/api/field/router.py
"""

from pathlib import Path

from app.api.field.router import router
from app.config import FIELD_DOCS_DIR
from app.core.database import insert_job_document
from app.services.field_access import assert_field_rep_owns_job
from app.services.inspection_summary import get_inspection_summary

FIELD_PHOTOS_DIR = Path("field_photos")

from app.api.field.documents import (
    download_field_evidence_grid,
    download_field_job_document,
    download_unsigned_contingency,
    get_field_job_documents,
    get_neighbor_letter,
)
from app.api.field.leads import (
    FieldClaimInfoPayload,
    FlagResolutionPayload,
    LeadIntakePayload,
    _sync_create_new_job,
    _sync_resolve_flag,
    create_new_job,
    get_field_job_details,
    get_field_pipeline_summary,
    list_my_jobs,
    resolve_flag,
    update_field_claim_info,
)
from app.api.field.photos import (
    upload_field_photo,
    upload_field_voice_note,
)
from app.api.field.radar import (
    get_field_storm_targets,
    get_zip_storms,
)
from app.api.field.sales_tools import (
    FieldReferralPayload,
    FieldReviewRequestPayload,
    field_add_referral,
    field_request_review,
    get_field_inspection_report,
    get_inspection_summary_route,
    get_sales_tools,
    resume_supplement,
    trigger_inspection_report,
)
from app.api.field.signatures import (
    ContingencySignaturePayload,
    PushSubscriptionKeys,
    PushSubscriptionPayload,
    RetailContractSignaturePayload,
    SignaturePayload,
    _sync_fetch_job_contingency,
    _sync_insert_agreement,
    _sync_process_image,
    contingency_sign,
    sign_retail_contract,
    subscribe_push_notifications,
)
