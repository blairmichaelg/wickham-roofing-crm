"""
End-to-End Domain Integration Test: Commercial Job Lifecycle.

Simulates the entire commercial roofing lifecycle from lead to close:
  1. Lead Creation (JobType.COMMERCIAL)
  2. Survey & Inspection Specification
  3. Schedule of Values (SOV) Architecture & Configurable Retainage Setup
  4. Contract Execution & Commercial PDF Generation
  5. Multi-Stage Progress Billing Cycles (AIA G702/G703 style)
  6. Business Rule Guardrails (Overbill > 100% rejection, excessive retainage release rejection)
  7. Substantial Completion & Last Work Date Tracking
  8. Final Retainage Release Application
  9. QuickBooks Online Progress Billing CSV Export & Reconciliation
  10. Final Account Close (zero balance, 100% completion)
"""

from __future__ import annotations

import datetime
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from app.core.constants import JobType
from app.core.database import (
    JobStatus,
    get_connection,
    run_migrations,
    update_job_status,
    upsert_financials,
)
from app.main import app
from app.services.pdf.commercial import CommercialPDFGenerator
from app.services.progress_billing_engine import (
    ProgressItemInput,
    SOVItem,
    compute_progress_billing,
)
from app.services.qbo_export import export_progress_billing_to_csv


@pytest.fixture
def api_client():
    client = TestClient(app)
    resp = client.post("/auth/login", data={"pin": "9999", "redirect_url": "/"}, follow_redirects=False)
    auth_cookie = resp.cookies.get("auth_token")
    client.cookies.set("auth_token", auth_cookie)
    return client


def test_full_commercial_job_lifecycle_end_to_end(api_client, tmp_path, monkeypatch):
    """
    Execute the entire lifecycle of a $100,000.00 commercial TPO roof restoration
    with a contracted 10.0% retainage holdback.
    """
    monkeypatch.setattr("app.services.qbo_export.EXPORT_DIR", tmp_path)
    run_migrations()

    job_id = "JOB-COMM-LIFE-2026"
    customer_name = "North Georgia Logistics Center"
    address = "4500 Logistics Parkway, Buford, GA 30518"

    conn = get_connection()
    try:
        # ---------------------------------------------------------------------
        # 1. Lead Intake: Create Commercial Job
        # ---------------------------------------------------------------------
        conn.execute(
            """
            INSERT OR REPLACE INTO jobs (
                id, homeowner_name, address_line1, city, state, postal_code,
                phone, materials_ordered, materials_on_site, status, job_type, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                customer_name,
                address,
                "Buford",
                "GA",
                "30518",
                "404-555-0199",
                0,
                0,
                JobStatus.LEAD_CAPTURED.value,
                JobType.COMMERCIAL.value,
                datetime.datetime.utcnow().isoformat(),
            ),
        )
        conn.commit()

        job_row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        assert job_row is not None
        assert job_row["job_type"] == "COMMERCIAL"

        # ---------------------------------------------------------------------
        # 2. Survey & Proposal Advancement
        # ---------------------------------------------------------------------
        update_job_status(job_id, JobStatus.RETAIL_QUOTE_GENERATED.value)
        cur_status = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()["status"]
        assert cur_status == JobStatus.RETAIL_QUOTE_GENERATED.value

        # ---------------------------------------------------------------------
        # 3. Schedule of Values (SOV) Architecture
        # ---------------------------------------------------------------------
        # Define 4 commercial trade line items totaling $100,000.00:
        # Item 1: Mobilization & Safety ($10,000)
        # Item 2: Tear-off & Deck Repair ($25,000)
        # Item 3: TPO Membrane & ISO Insulation ($55,000)
        # Item 4: Edge Metal & Coping ($10,000)
        sov_items = [
            SOVItem(id=f"{job_id}-sov-1", item_code="01-MOBIL", description="Mobilization, Rigging & Safety", scheduled_value_cents=1000000),
            SOVItem(id=f"{job_id}-sov-2", item_code="02-DEMO", description="Tear-off & Structural Deck Repair", scheduled_value_cents=2500000),
            SOVItem(id=f"{job_id}-sov-3", item_code="03-TPO", description="60-mil TPO Membrane & Polyiso Board", scheduled_value_cents=5500000),
            SOVItem(id=f"{job_id}-sov-4", item_code="04-METAL", description="Perimeter Edge Metal & Coping Cap", scheduled_value_cents=1000000),
        ]
        total_contract_cents = sum(i.scheduled_value_cents for i in sov_items)
        assert total_contract_cents == 10000000  # $100,000.00

        # Persist schedule (SOV items) into database
        for idx, item in enumerate(sov_items):
            conn.execute(
                """
                INSERT OR REPLACE INTO progress_billing_schedules (
                    id, job_id, item_code, description, scheduled_value_cents, order_index
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (item.id, job_id, item.item_code, item.description, item.scheduled_value_cents, idx),
            )
        conn.commit()

        # ---------------------------------------------------------------------
        # 4. Contract Signature & Commercial Platypus PDF Generation
        # ---------------------------------------------------------------------
        pdf_gen = CommercialPDFGenerator()
        contract_pdf_path = str(tmp_path / f"{job_id}_contract.pdf")
        contract_job_dict = {
            "id": job_id,
            "customer_name": customer_name,
            "property_address": address,
            "retainage_percent": 10.0,
            "contract_amount": 10000000,
        }
        sov_dicts = [
            {"item_code": i.item_code, "description": i.description, "scheduled_value_cents": i.scheduled_value_cents}
            for i in sov_items
        ]

        import asyncio
        asyncio.run(
            pdf_gen.generate_commercial_contract(
                job=contract_job_dict,
                sov_items=sov_dicts,
                terms={"retainage_percent": 10.0, "warranty_years": 20},
                filepath=contract_pdf_path,
            )
        )
        assert Path(contract_pdf_path).exists()
        assert Path(contract_pdf_path).stat().st_size > 1000
        update_job_status(job_id, JobStatus.RETAIL_CONTRACT_SIGNED.value)

        # ---------------------------------------------------------------------
        # 5. Progress Billing Cycle 1 (Mobilization 100%, Tear-off 40%)
        # ---------------------------------------------------------------------
        cycle_1_inputs = [
            ProgressItemInput(schedule_item_id=f"{job_id}-sov-1", work_completed_cents=1000000),  # $10,000 (100%)
            ProgressItemInput(schedule_item_id=f"{job_id}-sov-2", work_completed_cents=1000000),  # $10,000 (40%)
            ProgressItemInput(schedule_item_id=f"{job_id}-sov-3", work_completed_cents=0),
            ProgressItemInput(schedule_item_id=f"{job_id}-sov-4", work_completed_cents=0),
        ]
        app_1 = compute_progress_billing(
            schedule=sov_items,
            line_inputs=cycle_1_inputs,
            application_no=1,
            retainage_percent=10.0,
        )
        # Gross completed: $20,000
        # Retainage 10%: $2,000
        # Net payment due: $18,000
        assert app_1.total_completed_to_date_cents == 2000000
        assert app_1.current_payment_due_gross_cents == 2000000
        assert app_1.retainage_withheld_this_period_cents == 200000
        assert app_1.total_retainage_held_cents == 200000
        assert app_1.net_payment_due_cents == 1800000

        # ---------------------------------------------------------------------
        # 6. Progress Billing Cycle 2 (Tear-off 100%, TPO 50%)
        # ---------------------------------------------------------------------
        cycle_2_inputs = [
            ProgressItemInput(schedule_item_id=f"{job_id}-sov-1", work_completed_cents=0),
            ProgressItemInput(schedule_item_id=f"{job_id}-sov-2", work_completed_cents=1500000),  # remaining $15,000 (now 100%)
            ProgressItemInput(schedule_item_id=f"{job_id}-sov-3", work_completed_cents=2750000),  # $27,500 (50%)
            ProgressItemInput(schedule_item_id=f"{job_id}-sov-4", work_completed_cents=0),
        ]
        app_2 = compute_progress_billing(
            schedule=sov_items,
            line_inputs=cycle_2_inputs,
            application_no=2,
            retainage_percent=10.0,
            previous_applications=[app_1],
        )
        # Current period gross: $42,500
        # Cumulative completed: $62,500 (62.5%)
        # Current retainage withheld: $4,250
        # Total retainage held: $6,250
        # Net payment due this period: $38,250
        assert app_2.current_payment_due_gross_cents == 4250000
        assert app_2.total_completed_to_date_cents == 6250000
        assert app_2.overall_percent_complete == 62.5
        assert app_2.total_retainage_held_cents == 625000
        assert app_2.net_payment_due_cents == 3825000

        # ---------------------------------------------------------------------
        # 7. Business Rule Guardrails: Overbill Rejection (>100% of SOV)
        # ---------------------------------------------------------------------
        # Item 2 (Tear-off) is already at 100% ($25,000). Attempting to bill another $5,000 MUST raise ValueError.
        with pytest.raises(ValueError, match="cannot bill.*exceeds 100%"):
            compute_progress_billing(
                schedule=sov_items,
                line_inputs=[
                    ProgressItemInput(schedule_item_id=f"{job_id}-sov-2", work_completed_cents=500000),
                ],
                application_no=3,
                retainage_percent=10.0,
                previous_applications=[app_1, app_2],
            )

        # ---------------------------------------------------------------------
        # 8. Business Rule Guardrails: Premature / Excessive Retainage Release
        # ---------------------------------------------------------------------
        # Total retainage held is $6,250. Attempting to release $10,000 MUST raise ValueError.
        with pytest.raises(ValueError, match="Cannot release.*retainage.*currently held"):
            compute_progress_billing(
                schedule=sov_items,
                line_inputs=[],
                application_no=3,
                retainage_percent=10.0,
                previous_applications=[app_1, app_2],
                retainage_release_cents=1000000,  # $10,000 exceeds $6,250 held
            )

        # ---------------------------------------------------------------------
        # 9. Substantial Completion & Last Work Date
        # ---------------------------------------------------------------------
        # Complete remaining work: Membrane ($27,500) + Coping ($10,000)
        today_iso = datetime.date.today().isoformat()
        conn.execute("UPDATE jobs SET last_work_date = ? WHERE id = ?", (today_iso, job_id))
        conn.commit()
        upsert_financials(
            job_id=job_id,
            revenue_cents=10000000,
            carrier_rcv_cents=10000000,
            material_cost_cents=4000000,
            labor_cost_cents=3000000,
            overhead_pct=10.0,
            canvasser_commission_pct=5.0,
        )
        update_job_status(job_id, JobStatus.MATERIAL_ORDERED.value)
        update_job_status(job_id, JobStatus.MATERIALS_ON_SITE.value)
        update_job_status(job_id, JobStatus.INSTALL_SCHEDULED.value)
        update_job_status(job_id, JobStatus.INSTALL_COMPLETED.value)

        cycle_3_inputs = [
            ProgressItemInput(schedule_item_id=f"{job_id}-sov-1", work_completed_cents=0),
            ProgressItemInput(schedule_item_id=f"{job_id}-sov-2", work_completed_cents=0),
            ProgressItemInput(schedule_item_id=f"{job_id}-sov-3", work_completed_cents=2750000),  # 100% of membrane
            ProgressItemInput(schedule_item_id=f"{job_id}-sov-4", work_completed_cents=1000000),  # 100% of metal
        ]
        # At final completion, release the entire accumulated 10% retainage ($10,000)
        app_3_final = compute_progress_billing(
            schedule=sov_items,
            line_inputs=cycle_3_inputs,
            application_no=3,
            retainage_percent=10.0,
            previous_applications=[app_1, app_2],
            retainage_release_cents=1000000,  # Full retainage release!
        )
        assert app_3_final.total_completed_to_date_cents == 10000000  # 100% complete ($100,000)
        assert app_3_final.overall_percent_complete == 100.0
        assert app_3_final.total_retainage_held_cents == 0  # All retainage released!
        # Current gross: $37,500 - $3,750 retainage withheld + $10,000 retainage released = $43,750
        assert app_3_final.net_payment_due_cents == 4375000

        # Cumulative net payments across all 3 applications: $18,000 + $38,250 + $43,750 = $100,000.00 exactly!
        total_collected = app_1.net_payment_due_cents + app_2.net_payment_due_cents + app_3_final.net_payment_due_cents
        assert total_collected == 10000000, f"Expected exact contract sum $100k, got {total_collected}"

        # ---------------------------------------------------------------------
        # 10. QuickBooks Online Progress Billing CSV Export & Ledger Close
        # ---------------------------------------------------------------------
        qbo_items = [
            {"item_code": "01-MOBIL", "description": "Mobilization", "amount": 10000.00},
            {"item_code": "02-DEMO", "description": "Tear-off Part 1", "amount": 10000.00},
        ]
        csv_path = export_progress_billing_to_csv(
            job_id=job_id,
            application_no=1,
            line_items=qbo_items,
            customer_name=customer_name,
        )
        assert Path(csv_path).exists()
        csv_text = Path(csv_path).read_text(encoding="utf-8")
        assert f"PROG-{job_id[:6]}-APP1" in csv_text
        assert "01-MOBIL,Mobilization,1.00,10000.00,10000.00" in csv_text
        assert "02-DEMO,Tear-off Part 1,1.00,10000.00,10000.00" in csv_text

        # Advance through Invoicing and Payment Received to Closed
        update_job_status(job_id, JobStatus.INVOICED.value)
        update_job_status(job_id, JobStatus.RETAIL_PAYMENT_RECEIVED.value)

        # Record financials in database as reconciled integer cents
        upsert_financials(
            job_id=job_id,
            revenue_cents=10000000,
            carrier_rcv_cents=10000000,
            material_cost_cents=4000000,
            labor_cost_cents=3000000,
            overhead_pct=10.0,
            canvasser_commission_pct=5.0,
        )
        update_job_status(job_id, JobStatus.CLOSED.value)

        final_job = conn.execute("SELECT status, last_work_date FROM jobs WHERE id = ?", (job_id,)).fetchone()
        assert final_job["status"] == JobStatus.CLOSED.value
        assert final_job["last_work_date"] == today_iso

    finally:
        conn.close()
