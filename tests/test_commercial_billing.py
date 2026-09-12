"""Tests for Task 3: Commercial progress billing engine, retainage holdbacks, and SOV lifecycle."""

from pathlib import Path
import pytest

from app.core.constants import JobType
from app.core.database import get_connection, run_migrations
from app.services.progress_billing_engine import (
    SOVItem,
    ProgressItemInput,
    compute_progress_billing,
)
from app.services.qbo_export import export_progress_billing_to_csv


def test_commercial_job_type_enum():
    """Verify JobType has COMMERCIAL distinct from INSURANCE and RETAIL."""
    assert JobType.COMMERCIAL == "COMMERCIAL"
    assert JobType.INSURANCE == "INSURANCE"
    assert JobType.RETAIL == "RETAIL"


def test_migration_0026_progress_billing_tables():
    """Verify migration 26 creates tables for SOV and applications."""
    run_migrations()
    conn = get_connection()
    try:
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'progress_billing_%'"
            ).fetchall()
        ]
        assert "progress_billing_schedules" in tables
        assert "progress_billing_applications" in tables
        assert "progress_billing_items" in tables

        # Verify last_work_date column in jobs
        cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()}
        assert "last_work_date" in cols
    finally:
        conn.close()


def test_progress_billing_lifecycle_with_configurable_retainage():
    """Test full multi-cycle progress billing lifecycle:
    1. Cycle 1: 50% framing, 30% membrane with 10% retainage
    2. Cycle 2: Remaining 50% framing, 50% membrane with 10% retainage
    3. Cycle 3: 100% completion, retainage release
    """
    schedule = [
        SOVItem(id="sov-1", item_code="01-DEMO", description="Tear Off & Deck Prep", scheduled_value_cents=500000),  # $5,000
        SOVItem(id="sov-2", item_code="02-MEMB", description="TPO Membrane & Insulation", scheduled_value_cents=1500000),  # $15,000
        SOVItem(id="sov-3", item_code="03-METL", description="Coping & Edge Metal", scheduled_value_cents=200000),  # $2,000
    ]
    total_sov = 500000 + 1500000 + 200000  # $22,000

    # === Cycle 1 ===
    # 50% of demo ($2,500), 20% of membrane ($3,000)
    cycle_1_inputs = [
        ProgressItemInput(schedule_item_id="sov-1", work_completed_cents=250000, stored_materials_cents=0),
        ProgressItemInput(schedule_item_id="sov-2", work_completed_cents=300000, stored_materials_cents=0),
        ProgressItemInput(schedule_item_id="sov-3", work_completed_cents=0, stored_materials_cents=0),
    ]
    # Configured retainage: 10.0%
    app1 = compute_progress_billing(
        schedule=schedule,
        line_inputs=cycle_1_inputs,
        application_no=1,
        retainage_percent=10.0,
    )
    assert app1.current_payment_due_gross_cents == 550000  # $5,500.00
    assert app1.retainage_withheld_this_period_cents == 55000  # 10% of $5,500 = $550.00
    assert app1.net_payment_due_cents == 495000  # $4,950.00
    assert app1.total_retainage_held_cents == 55000
    assert app1.overall_percent_complete == 25.0  # $5,500 / $22,000 = 25%

    # === Cycle 2 ===
    # Remaining 50% of demo ($2,500), 60% more of membrane ($9,000), 50% metal ($1,000)
    cycle_2_inputs = [
        ProgressItemInput(schedule_item_id="sov-1", work_completed_cents=250000, stored_materials_cents=0),
        ProgressItemInput(schedule_item_id="sov-2", work_completed_cents=900000, stored_materials_cents=0),
        ProgressItemInput(schedule_item_id="sov-3", work_completed_cents=100000, stored_materials_cents=0),
    ]
    app2 = compute_progress_billing(
        schedule=schedule,
        line_inputs=cycle_2_inputs,
        application_no=2,
        retainage_percent=10.0,
        previous_applications=[app1],
    )
    assert app2.current_payment_due_gross_cents == 1250000  # $12,500.00
    assert app2.retainage_withheld_this_period_cents == 125000  # $1,250.00
    assert app2.net_payment_due_cents == 1125000  # $11,250.00
    assert app2.total_retainage_held_cents == 55000 + 125000  # $1,800.00
    assert app2.total_completed_to_date_cents == 550000 + 1250000  # $18,000.00
    assert app2.overall_percent_complete == round((1800000 / total_sov) * 100, 2)

    # === Cycle 3 (Final with Retainage Release) ===
    # Remaining membrane ($3,000) and metal ($1,000) = $4,000 gross
    cycle_3_inputs = [
        ProgressItemInput(schedule_item_id="sov-1", work_completed_cents=0, stored_materials_cents=0),
        ProgressItemInput(schedule_item_id="sov-2", work_completed_cents=300000, stored_materials_cents=0),
        ProgressItemInput(schedule_item_id="sov-3", work_completed_cents=100000, stored_materials_cents=0),
    ]
    # Retainage released: full held retainage ($1,800 + $400 from this period = $2,200 total)
    app3 = compute_progress_billing(
        schedule=schedule,
        line_inputs=cycle_3_inputs,
        application_no=3,
        retainage_percent=10.0,
        previous_applications=[app1, app2],
        retainage_release_cents=220000,  # release all $2,200 retainage
    )
    assert app3.current_payment_due_gross_cents == 400000
    assert app3.retainage_withheld_this_period_cents == 40000
    assert app3.retainage_released_this_period_cents == 220000
    assert app3.total_retainage_held_cents == 0  # all released
    # Net due = (400,000 - 40,000) + 220,000 = 580,000
    assert app3.net_payment_due_cents == 580000
    assert app3.overall_percent_complete == 100.0
    assert app3.total_completed_to_date_cents == total_sov


def test_cannot_bill_more_than_100_percent():
    """Enforce strict business rule: billing cannot exceed 100% of Schedule of Values."""
    schedule = [
        SOVItem(id="sov-1", item_code="ROOF-01", description="Commercial Flat Roof", scheduled_value_cents=1000000),  # $10,000
    ]
    # Attempting to bill $10,001.00
    overbill_inputs = [
        ProgressItemInput(schedule_item_id="sov-1", work_completed_cents=1000100, stored_materials_cents=0),
    ]
    with pytest.raises(ValueError) as excinfo:
        compute_progress_billing(
            schedule=schedule,
            line_inputs=overbill_inputs,
            application_no=1,
            retainage_percent=10.0,
        )
    assert "Schedule of Values overbill error" in str(excinfo.value)
    assert "cannot bill $10001.00" in str(excinfo.value)


def test_custom_retainage_override_percentage():
    """Verify retainage calculates against whatever retainage_percent is configured (e.g. 5.0% or 15.0%)."""
    schedule = [
        SOVItem(id="sov-1", item_code="ROOF-01", description="Commercial Shingles", scheduled_value_cents=5000000),  # $50,000
    ]
    line_inputs = [
        ProgressItemInput(schedule_item_id="sov-1", work_completed_cents=1000000, stored_materials_cents=0),  # $10,000
    ]

    # Test with 5.0% retainage
    app_5pct = compute_progress_billing(
        schedule=schedule,
        line_inputs=line_inputs,
        application_no=1,
        retainage_percent=5.0,
    )
    assert app_5pct.retainage_withheld_this_period_cents == 50000  # $500.00
    assert app_5pct.net_payment_due_cents == 950000  # $9,500.00

    # Test with 15.0% retainage
    app_15pct = compute_progress_billing(
        schedule=schedule,
        line_inputs=line_inputs,
        application_no=1,
        retainage_percent=15.0,
    )
    assert app_15pct.retainage_withheld_this_period_cents == 150000  # $1,500.00
    assert app_15pct.net_payment_due_cents == 850000  # $8,500.00


def test_qbo_progress_billing_csv_export(tmp_path, monkeypatch):
    """Verify QBO export generates clean commercial progress billing CSVs."""
    monkeypatch.setattr("app.services.qbo_export.EXPORT_DIR", tmp_path)

    items = [
        {"item_code": "Commercial:Roofing", "description": "Phase 1 Flat Decking", "amount": 15000.00},
        {"item_code": "Commercial:Insulation", "description": "ISO Board Installation", "amount": 8500.00},
    ]

    csv_path = export_progress_billing_to_csv(
        job_id="test-job-commercial-12345",
        application_no=2,
        line_items=items,
        customer_name="Grandview Corporate Center",
    )

    content = Path(csv_path).read_text(encoding="utf-8")
    assert "PROG-TEST-J-APP2,Grandview Corporate Center" in content
    assert "Phase 1 Flat Decking,1.00,15000.00,15000.00" in content
    assert "ISO Board Installation,1.00,8500.00,8500.00" in content
