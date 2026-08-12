"""
V4 Independent CRM Local Database
Manages the SQLite connection and state machine for the local pipeline.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime
from enum import StrEnum
from pathlib import Path

import structlog
from passlib.context import CryptContext

from app.config import get_settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

logger = structlog.get_logger("app.core.database")

def get_db_path() -> Path:
    """
    Get Db Path functionality.
    
    Returns:
        Path: The resulting output.
    """
    return Path(get_settings().get_db_path)

class JobStatus(StrEnum):
    # PROCESSING STATES (ARQ workers may write these autonomously)
    """JobStatus definition."""
    LEAD_CAPTURED = "LEAD_CAPTURED"
    CONTINGENCY_SIGNED = "CONTINGENCY_SIGNED"
    RETAIL_CONTRACT_SIGNED = "RETAIL_CONTRACT_SIGNED"
    CLAIM_FILED = "CLAIM_FILED"
    ADJUSTER_MEETING_COMPLETED = "ADJUSTER_MEETING_COMPLETED"
    PHOTOS_UPLOADED = "PHOTOS_UPLOADED"
    EV_ORDERED = "EV_ORDERED"
    EV_PARSED = "EV_PARSED"
    STATEMENT_OF_LOSS_RECEIVED = "STATEMENT_OF_LOSS_RECEIVED"
    PENDING_OPERATOR_REVIEW = "PENDING_OPERATOR_REVIEW"
    PIPELINE_FAILED = "PIPELINE_FAILED"
    INSPECTION_FAILED = "INSPECTION_FAILED"

    # BUSINESS STATES (Operator-only manual gates)
    SUPPLEMENT_GENERATED = "SUPPLEMENT_GENERATED"
    SUPPLEMENT_SUBMITTED = "SUPPLEMENT_SUBMITTED"
    SUPPLEMENT_DENIED = "SUPPLEMENT_DENIED"
    SUPPLEMENT_APPROVED = "SUPPLEMENT_APPROVED"
    SCOPE_APPROVED = "SCOPE_APPROVED"
    MATERIAL_ORDERED = "MATERIAL_ORDERED"
    MATERIALS_ON_SITE = "MATERIALS_ON_SITE"
    INSTALL_SCHEDULED = "INSTALL_SCHEDULED"
    INSTALL_COMPLETED = "INSTALL_COMPLETED"
    INSPECTION_COMPLETED = "INSPECTION_COMPLETED"
    FINAL_INSPECTION = "FINAL_INSPECTION"
    FINAL_INSPECTION_COMPLETED = "FINAL_INSPECTION_COMPLETED"
    INVOICED = "INVOICED"
    PAYMENT_RECEIVED = "PAYMENT_RECEIVED"
    CLOSED = "CLOSED"
    
    # RETAIL STATES
    RETAIL_QUOTE_GENERATED = "RETAIL_QUOTE_GENERATED"
    RETAIL_QUOTE_ACCEPTED  = "RETAIL_QUOTE_ACCEPTED"
    RETAIL_QUOTE_DECLINED  = "RETAIL_QUOTE_DECLINED"
    
    # OTHER
    AWAITING_CARRIER_RESPONSE = "AWAITING_CARRIER_RESPONSE"
    APPRAISAL_INVOKED = "APPRAISAL_INVOKED"
    CLAIM_DENIED = "CLAIM_DENIED"  # Primary claim denied outright — no SoL issued
    # Payment milestone statuses
    ACV_PAYMENT_RECEIVED = "ACV_PAYMENT_RECEIVED"           # First check from carrier
    DEPRECIATION_PAYMENT_RECEIVED = "DEPRECIATION_PAYMENT_RECEIVED"  # Recoverable depreciation released
    RETAIL_PAYMENT_RECEIVED = "RETAIL_PAYMENT_RECEIVED"    # Single retail payment received
    
    @classmethod
    def is_operator_gate(cls, status: JobStatus) -> bool:
        """
        Is Operator Gate functionality.
        
        Args:
                cls (Any): cls parameter.
                status ('JobStatus'): status parameter.
        
        Returns:
            bool: The resulting output.
        """
        _OPERATOR_GATES = {
            cls.SUPPLEMENT_GENERATED, cls.SUPPLEMENT_SUBMITTED,
            cls.SUPPLEMENT_DENIED, cls.SUPPLEMENT_APPROVED,
            cls.SCOPE_APPROVED, cls.MATERIAL_ORDERED,
            cls.MATERIALS_ON_SITE, cls.INSTALL_SCHEDULED,
            cls.INSTALL_COMPLETED, cls.INSPECTION_COMPLETED,
            cls.FINAL_INSPECTION, cls.FINAL_INSPECTION_COMPLETED,
            cls.INVOICED, cls.PAYMENT_RECEIVED, cls.CLOSED,
            cls.RETAIL_QUOTE_GENERATED, cls.RETAIL_QUOTE_ACCEPTED,
            cls.RETAIL_QUOTE_DECLINED, cls.APPRAISAL_INVOKED,
            cls.EV_ORDERED, cls.ACV_PAYMENT_RECEIVED,
            cls.DEPRECIATION_PAYMENT_RECEIVED, cls.RETAIL_PAYMENT_RECEIVED
        }
        return status in _OPERATOR_GATES

def _configure_connection(conn: sqlite3.Connection) -> None:
    """Configure PRAGMA settings for maximum WAL concurrency."""
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA temp_store=MEMORY;")
    conn.execute("PRAGMA mmap_size=268435456;")
    conn.execute("PRAGMA busy_timeout=15000;")

def get_connection() -> sqlite3.Connection:
    """Get a SQLite connection with WAL mode enabled for concurrency."""
    conn = sqlite3.connect(get_db_path(), check_same_thread=False, timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.isolation_level = None  # Explicit transaction control
    _configure_connection(conn)
    return conn

def _fetch_job_sync(job_id: str) -> dict | None:
    """
    Fetch a complete job record synchronously.
    """
    conn = get_connection()
    try:
        cursor = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        row = cursor.fetchone()
        if not row:
            return None
        job_dict = dict(row)
        sv = conn.execute("SELECT loss_date FROM storm_verifications WHERE job_id = ?", (job_id,)).fetchone()
        if sv and sv["loss_date"]:
            job_dict["loss_date"] = sv["loss_date"]
        return job_dict
    finally:
        conn.close()

def run_migrations() -> None:
    """Run versioned migrations."""
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute('''
            CREATE TABLE IF NOT EXISTS schema_version (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                version INTEGER NOT NULL DEFAULT 0,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.execute("INSERT OR IGNORE INTO schema_version (id, version) VALUES (1, 0)")
        
        row = conn.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()
        current_version = row["version"] if row else 0
        
        # Apply migrations
        if current_version < 1:
            import importlib
            m1 = importlib.import_module("app.core.migrations.0001_initial_schema")
            m1.up(conn)
            
            conn.execute("UPDATE schema_version SET version = 1, applied_at = CURRENT_TIMESTAMP WHERE id = 1")
            
        if current_version < 2:
            import importlib
            m2 = importlib.import_module("app.core.migrations.0002_manual_flashing")
            m2.up(conn)
            
            conn.execute("UPDATE schema_version SET version = 2, applied_at = CURRENT_TIMESTAMP WHERE id = 1")

        if current_version < 3:
            import importlib
            m3 = importlib.import_module("app.core.migrations.0003_hash_field_rep_pins")
            m3.up(conn)
            
            conn.execute("UPDATE schema_version SET version = 3, applied_at = CURRENT_TIMESTAMP WHERE id = 1")

        if current_version < 4:
            import importlib
            m4 = importlib.import_module("app.core.migrations.0004_add_commission_overrides")
            m4.up(conn)
            
            conn.execute("UPDATE schema_version SET version = 4, applied_at = CURRENT_TIMESTAMP WHERE id = 1")

        if current_version < 5:
            import importlib
            m5 = importlib.import_module("app.core.migrations.0005_add_document_visibility")
            m5.up(conn)
            
            conn.execute("UPDATE schema_version SET version = 5, applied_at = CURRENT_TIMESTAMP WHERE id = 1")

        if current_version < 6:
            import importlib
            m6 = importlib.import_module("app.core.migrations.0006_add_damage_and_storm_events")
            m6.up(conn)
            
            conn.execute("UPDATE schema_version SET version = 6, applied_at = CURRENT_TIMESTAMP WHERE id = 1")
            
        if current_version < 7:
            import importlib
            m7 = importlib.import_module("app.core.migrations.0007_integer_cents")
            m7.up(conn)
            
            conn.execute("UPDATE schema_version SET version = 7, applied_at = CURRENT_TIMESTAMP WHERE id = 1")

        if current_version < 8:
            import importlib
            m8 = importlib.import_module("app.core.migrations.0008_commission_ready")
            m8.up(conn)
            
            conn.execute("UPDATE schema_version SET version = 8, applied_at = CURRENT_TIMESTAMP WHERE id = 1")

        if current_version < 9:
            import importlib
            m9 = importlib.import_module("app.core.migrations.0009_drop_real_financials")
            m9.up(conn)
            
            conn.execute("UPDATE schema_version SET version = 9, applied_at = CURRENT_TIMESTAMP WHERE id = 1")

        if current_version < 10:
            import importlib
            m10 = importlib.import_module("app.core.migrations.0010_drop_legacy_real_columns")
            m10.up(conn)
            
            conn.execute("UPDATE schema_version SET version = 10, applied_at = CURRENT_TIMESTAMP WHERE id = 1")

        if current_version < 11:
            import importlib
            m11 = importlib.import_module("app.core.migrations.0011_add_depreciation_net_claim")
            m11.up(conn)
            
            conn.execute("UPDATE schema_version SET version = 11, applied_at = CURRENT_TIMESTAMP WHERE id = 1")

        if current_version < 12:
            import importlib
            m12 = importlib.import_module("app.core.migrations.0012_add_latitude_longitude_to_storm_events")
            m12.up(conn)
            
            conn.execute("UPDATE schema_version SET version = 12, applied_at = CURRENT_TIMESTAMP WHERE id = 1")

        if current_version < 13:
            import importlib
            m13 = importlib.import_module("app.core.migrations.0013_add_shingle_and_schedule_columns")
            m13.up(conn)
            
            conn.execute("UPDATE schema_version SET version = 13, applied_at = CURRENT_TIMESTAMP WHERE id = 1")

        if current_version < 14:
            import importlib
            m14 = importlib.import_module("app.core.migrations.0014_add_loss_date_to_jobs")
            m14.up(conn)
            
            conn.execute("UPDATE schema_version SET version = 14, applied_at = CURRENT_TIMESTAMP WHERE id = 1")

        if current_version < 15:
            import importlib
            m15 = importlib.import_module("app.core.migrations.0015_add_claim_pipeline_timestamps")
            m15.up(conn)
            conn.execute("UPDATE schema_version SET version = 15, applied_at = CURRENT_TIMESTAMP WHERE id = 1")

        if current_version < 16:
            import importlib
            m16 = importlib.import_module("app.core.migrations.0016_add_payment_tracking")
            m16.up(conn)
            conn.execute("UPDATE schema_version SET version = 16, applied_at = CURRENT_TIMESTAMP WHERE id = 1")

        conn.execute("COMMIT")
        logger.info("migrations_applied", current_version=current_version, target_version=16)
        
        # Since seed logic was removed from up(), do it here outside the transaction
        if current_version < 1:
            seed_default_pricing()
            seed_supplement_rules()
        seed_core_team_reps()
    except Exception as e:
        conn.execute("ROLLBACK")
        logger.error("migration_failed", error=str(e))
        raise
    finally:
        conn.close()

def seed_default_pricing() -> None:
    """Seed the pricing table with baseline material/labor rates.
    
    Inserts default Wickham Roofing pricing values if they do not already exist.
    """
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        # Default Wickham Roofing baselines
        baseline_pricing = [
            ("field_shingle_bundles", 105.0),
            ("starter_bundles", 45.0),
            ("hip_ridge_bundles", 60.0),
            ("ice_and_water_rolls", 90.0),
            ("synthetic_underlayment_rolls", 65.0),
            ("drip_edge_pieces_10ft", 15.0),
            ("step_flashing_tins", 0.50),
            ("coil_nails_boxes", 35.0),
            ("plastic_cap_nails_boxes", 25.0),
            ("roof_sealant_tubes", 7.0),
            ("pipe_jacks", 20.0),
            ("exhaust_vents", 45.0),
            ("ridge_vent_rolls_20ft", 80.0),
            ("retail_standard_per_sq", 350.0),
            ("retail_arch_per_sq", 420.0),
            ("retail_premium_per_sq", 580.0),
        ]
        conn.executemany('''
            INSERT OR IGNORE INTO pricing (item_key, default_rate, default_rate_cents)
            VALUES (?, ?, ?)
        ''', [(row[0], row[1], int(round(row[1] * 100))) for row in baseline_pricing])
        conn.execute("COMMIT")
    except Exception as e:
        logger.error("pricing_seed_failed", error=str(e))

def seed_supplement_rules() -> None:
    """Seed the supplement_rules table with baseline rules."""
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        baseline_rules = [
            ("synthetic_math_rule", "CARRIER", "MATH", "Carrier Line Item Mathematical Verification", "INTERNAL_POLICY", "eval_carrier_math", False),
            (str(uuid.uuid4()), "RFG 300S", "RFG START", "Manufacturer Shingle High-Wind Installation Specifications", "MFG_SPEC", "eval_rfg_start", False),
            (str(uuid.uuid4()), "RFG 300S", "RFG DRIP", "IRC R905.2.8.5", "IRC", "eval_rfg_drip", False),
            (str(uuid.uuid4()), "RFG 300S", "RFG IWS", "IRC R905.1.2", "IRC", "eval_rfg_iws", True),
            (str(uuid.uuid4()), "RFG TEAR", "DMO PU", "Debris Haul-off and Tonnage Regulatory Compliance", "INTERNAL_POLICY", "eval_dmo_pu", False)
        ]
        conn.executemany('''
            INSERT OR IGNORE INTO supplement_rules (id, parent_code, required_child_code, citation_text, citation_type, trigger_logic_name, climate_dependent)
            SELECT ?, ?, ?, ?, ?, ?, ?
            WHERE NOT EXISTS (
                SELECT 1 FROM supplement_rules 
                WHERE parent_code = ? AND required_child_code = ?
            )
        ''', [(r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[1], r[2]) for r in baseline_rules])
        conn.execute("COMMIT")
    except Exception as e:
        logger.error("supplement_rules_seed_failed", error=str(e))
    finally:
        conn.close()

def seed_core_team_reps() -> None:
    """Ensure Michael, Scott, and Debi exist in field_reps."""
    conn = get_connection()
    try:
        core_members = [
            ("rep-michael", "Michael", "7194"),
            ("rep-scott", "Scott", "4826"),
            ("rep-debi", "Debi", "6315"),
        ]
        for rep_id, name, default_pin in core_members:
            row = conn.execute("SELECT id FROM field_reps WHERE name = ?", (name,)).fetchone()
            if not row:
                pin_hash = pwd_context.hash(default_pin)
                conn.execute(
                    """INSERT OR IGNORE INTO field_reps (id, name, pin_hash, is_active)
                       VALUES (?, ?, ?, 1)""",
                    (rep_id, name, pin_hash)
                )
        conn.commit()
    except Exception as e:
        logger.error("core_team_reps_seed_failed", error=str(e))
    finally:
        conn.close()

def get_pricing_ledger() -> dict[str, float]:
    """Fetch all default rates from the pricing table.
    
    Returns:
        dict[str, float]: A dictionary mapping item keys to their default rates.
    """
    conn = get_connection()
    try:
        cursor = conn.execute("SELECT item_key, default_rate_cents / 100.0 as default_rate FROM pricing")
        return {row["item_key"]: row["default_rate"] for row in cursor}
    except Exception as e:
        logger.error("failed_to_fetch_pricing", error=str(e))
        return {}
    finally:
        conn.close()

def _update_job_status_internal(conn: sqlite3.Connection, job_id: str, new_status: str, note: str = "") -> None:
    """Internal method to update job status inside an existing transaction."""
    try:
        JobStatus(new_status)
    except ValueError:
        raise ValueError(f"Invalid job status: {new_status}")

    # Get current status history
    cursor = conn.execute("SELECT status, status_history FROM jobs WHERE id = ?", (job_id,))
    row = cursor.fetchone()
    if not row:
        raise ValueError(f"Job {job_id} not found.")

    current_status = row["status"]

    # ---------------------------------------------------------
    # STATE MACHINE ENFORCEMENT
    # ---------------------------------------------------------
    if new_status == JobStatus.SUPPLEMENT_APPROVED:
        if current_status not in [
            JobStatus.AWAITING_CARRIER_RESPONSE,
            JobStatus.SUPPLEMENT_SUBMITTED
        ]:
            raise RuntimeError(
                "ILLEGAL TRANSITION: SUPPLEMENT_APPROVED requires "
                "job to be in AWAITING_CARRIER_RESPONSE or "
                "SUPPLEMENT_SUBMITTED."
            )

    elif new_status == JobStatus.SUPPLEMENT_DENIED:
        if current_status not in [
            JobStatus.AWAITING_CARRIER_RESPONSE,
            JobStatus.SUPPLEMENT_SUBMITTED
        ]:
            raise RuntimeError(
                "ILLEGAL TRANSITION: SUPPLEMENT_DENIED requires "
                "job to be in AWAITING_CARRIER_RESPONSE or "
                "SUPPLEMENT_SUBMITTED."
            )
            
    elif new_status == JobStatus.MATERIAL_ORDERED:
        fin_cursor = conn.execute("SELECT revenue_cents FROM financials WHERE job_id = ?", (job_id,))
        if not fin_cursor.fetchone():
            raise RuntimeError("ILLEGAL TRANSITION: Cannot order materials without calculated financials.")
    elif new_status == JobStatus.INSTALL_SCHEDULED:
        sched_cursor = conn.execute(
            "SELECT status FROM jobs WHERE id = ?", (job_id,)
        )
        row = sched_cursor.fetchone()
        if not row or row["status"] != JobStatus.MATERIALS_ON_SITE:
            raise RuntimeError(
                "ILLEGAL TRANSITION: Cannot schedule install until "
                "MATERIALS_ON_SITE is confirmed."
            )
    elif new_status == JobStatus.INSTALL_COMPLETED:
        if current_status != JobStatus.INSTALL_SCHEDULED:
            raise RuntimeError(
                f"ILLEGAL TRANSITION: Cannot mark install complete from status {current_status}. Must be INSTALL_SCHEDULED."
            )
    elif new_status == JobStatus.FINAL_INSPECTION:
        if current_status != JobStatus.INSTALL_COMPLETED:
            raise RuntimeError(
                f"ILLEGAL TRANSITION: Cannot require final inspection from status {current_status}. Must be INSTALL_COMPLETED."
            )
    elif new_status == JobStatus.FINAL_INSPECTION_COMPLETED:
        if current_status != JobStatus.FINAL_INSPECTION:
            raise RuntimeError(
                f"ILLEGAL TRANSITION: Cannot mark final inspection completed from status {current_status}. Must be FINAL_INSPECTION."
            )

    elif new_status == JobStatus.INVOICED:
        # Ensure the pipeline doesn't invoice a lead that wasn't built
        valid_priors = [
            JobStatus.MATERIAL_ORDERED,
            JobStatus.INSTALL_SCHEDULED,
            JobStatus.INSTALL_COMPLETED,
            JobStatus.FINAL_INSPECTION,
            JobStatus.INSPECTION_COMPLETED,
            JobStatus.FINAL_INSPECTION_COMPLETED,
            JobStatus.INVOICED
        ]
        if current_status not in valid_priors:
            raise RuntimeError(f"ILLEGAL TRANSITION: Cannot invoice from state {current_status}.")

    elif new_status in [JobStatus.ACV_PAYMENT_RECEIVED, JobStatus.DEPRECIATION_PAYMENT_RECEIVED, JobStatus.RETAIL_PAYMENT_RECEIVED]:
        if current_status not in [
            JobStatus.INVOICED,
            JobStatus.ACV_PAYMENT_RECEIVED,
            JobStatus.DEPRECIATION_PAYMENT_RECEIVED,
            JobStatus.RETAIL_PAYMENT_RECEIVED
        ]:
            raise RuntimeError(f"ILLEGAL TRANSITION: Cannot receive payment in state {current_status}.")

    elif new_status == JobStatus.PAYMENT_RECEIVED:
        comm_cursor = conn.execute("SELECT commission_generated_at FROM jobs WHERE id = ?", (job_id,))
        comm_row = comm_cursor.fetchone()
        if not comm_row or not comm_row["commission_generated_at"]:
            timestamp_str_comm = __import__('datetime').datetime.now(__import__('datetime').timezone.utc).replace(tzinfo=None).isoformat() + "Z"
            conn.execute(
                "UPDATE jobs SET commission_ready = 1, commission_generated_at = ? WHERE id = ?",
                (timestamp_str_comm, job_id)
            )

    elif new_status == JobStatus.CLOSED:
        if current_status not in [JobStatus.PAYMENT_RECEIVED, JobStatus.RETAIL_PAYMENT_RECEIVED, JobStatus.DEPRECIATION_PAYMENT_RECEIVED]:
            raise RuntimeError("ILLEGAL TRANSITION: Cannot close job before PAYMENT_RECEIVED.")
    # ---------------------------------------------------------

    # Update DB with atomic JSON append to prevent race conditions
    timestamp_str = datetime.now(__import__('datetime').timezone.utc).replace(tzinfo=None).isoformat() + "Z"
    cursor = conn.execute(
        """
        UPDATE jobs 
        SET status = ?, 
            status_history = json_insert(
                COALESCE(status_history, '[]'), 
                '$[#]', 
                json_object('status', ?, 'timestamp', ?, 'note', ?)
            )
        WHERE id = ?
        """,
        (new_status, new_status, timestamp_str, note, job_id)
    )
        
    if new_status == JobStatus.INVOICED:
        conn.execute("UPDATE jobs SET invoiced_at = CURRENT_TIMESTAMP WHERE id = ?", (job_id,))
        
    if cursor.rowcount == 0:
        raise ValueError(f"Job {job_id} not found during update")

def force_override_status(job_id: str, new_status: str, note: str = "") -> None:
    """
    Admin-only emergency override.
    Bypasses all state machine rules to forcefully set the status.
    Appends an OVERRIDE note to the status_history.
    """
    try:
        JobStatus(new_status)
    except ValueError:
        raise ValueError(f"Invalid job status: {new_status}")

    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        timestamp_str = __import__('datetime').datetime.now(__import__('datetime').timezone.utc).replace(tzinfo=None).isoformat() + "Z"
        cursor = conn.execute(
            """
            UPDATE jobs 
            SET status = ?, 
                status_history = json_insert(
                    COALESCE(status_history, '[]'), 
                    '$[#]', 
                    json_object('status', ?, 'timestamp', ?, 'note', ?)
                )
            WHERE id = ?
            """,
            (new_status, new_status, timestamp_str, f"ADMIN OVERRIDE: {note}".strip(), job_id)
        )
        if cursor.rowcount == 0:
            raise ValueError(f"Job {job_id} not found.")
        conn.execute("COMMIT")
        logger.warning("job_status_force_overridden", job_id=job_id, new_status=new_status)
    except Exception as e:
        conn.execute("ROLLBACK")
        logger.error("job_status_force_override_failed", job_id=job_id, error=str(e))
        raise
    finally:
        conn.close()

def update_job_status(job_id: str, new_status: str, note: str = "") -> None:
    """Enforces logical state transitions and appends to the JSON status history."""
    try:
        JobStatus(new_status)
    except ValueError:
        raise ValueError(f"Invalid job status: {new_status}")
        
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        _update_job_status_internal(conn, job_id, new_status, note)
        conn.execute("COMMIT")
        logger.info("job_status_updated", job_id=job_id, status=new_status)
    except Exception as e:
        conn.execute("ROLLBACK")
        logger.error("job_status_update_failed", job_id=job_id, error=str(e))
        raise
    finally:
        conn.close()

def upsert_financials(
    job_id: str, 
    revenue_cents: int, 
    carrier_rcv_cents: int, 
    material_cost_cents: int, 
    labor_cost_cents: int, 
    overhead_pct: float, 
    canvasser_commission_pct: float,
    permits_fee_cents: int = 0,
    deductible_cents: int = 0,
    acv_payment_cents: int = 0,
    recoverable_depreciation_cents: int = 0,
    depreciation_cents: int | None = None,
    net_claim_cents: int | None = None
) -> None:
    """Upsert financial pre-build parameters into the financials table.

    Args:
        job_id (str): The unique identifier for the job.
        revenue_cents (int): Total contract price or revenue in cents.
        carrier_rcv_cents (int): The carrier's Replacement Cost Value in cents.
        material_cost_cents (int): Total material cost in cents.
        labor_cost_cents (int): Total labor cost in cents.
        overhead_pct (float): Overhead percentage.
        canvasser_commission_pct (float): Commission percentage.
        permits_fee_cents (int): Cost of permits in cents.
        
    Raises:
        Exception: If the upsert operation fails.
    """
    if depreciation_cents is None:
        depreciation_cents = recoverable_depreciation_cents
    if net_claim_cents is None:
        net_claim_cents = max(0, carrier_rcv_cents - depreciation_cents - deductible_cents)

    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        
        conn.execute('''
            INSERT INTO financials 
            (job_id, 
             revenue_cents, carrier_rcv_cents, material_cost_cents, labor_cost_cents, permits_fee_cents,
             overhead_pct, canvasser_commission_pct, deductible_cents, acv_payment_cents, recoverable_depreciation_cents,
             depreciation_cents, net_claim_cents)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                revenue_cents = excluded.revenue_cents,
                carrier_rcv_cents = excluded.carrier_rcv_cents,
                material_cost_cents = excluded.material_cost_cents,
                labor_cost_cents = excluded.labor_cost_cents,
                permits_fee_cents = excluded.permits_fee_cents,
                overhead_pct = excluded.overhead_pct,
                canvasser_commission_pct = excluded.canvasser_commission_pct,
                deductible_cents = excluded.deductible_cents,
                acv_payment_cents = excluded.acv_payment_cents,
                recoverable_depreciation_cents = excluded.recoverable_depreciation_cents,
                depreciation_cents = excluded.depreciation_cents,
                net_claim_cents = excluded.net_claim_cents
        ''', (
            job_id, 
            revenue_cents, carrier_rcv_cents, material_cost_cents, labor_cost_cents, permits_fee_cents,
            overhead_pct, canvasser_commission_pct, deductible_cents, acv_payment_cents, recoverable_depreciation_cents,
            depreciation_cents, net_claim_cents
        ))
        conn.execute("COMMIT")
        logger.info("financials_upserted", job_id=job_id)
    except Exception as e:
        logger.error("financials_upsert_failed", job_id=job_id, error=str(e))
        raise
    finally:
        conn.close()

def insert_material_order(job_id: str, supplier_name: str, delivery_date: str, bom_json: str) -> None:
    """Insert a material order and generate a UUID for the record.

    Args:
        job_id (str): The unique identifier for the job.
        supplier_name (str): The name of the material supplier.
        delivery_date (str): The requested delivery date.
        bom_json (str): The bill of materials encoded as a JSON string.
        
    Raises:
        Exception: If the database insertion fails.
    """
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        order_id = str(uuid.uuid4())
        conn.execute('''
            INSERT INTO material_orders (id, job_id, supplier_name, delivery_date, bom_json)
            VALUES (?, ?, ?, ?, ?)
        ''', (order_id, job_id, supplier_name, delivery_date, bom_json))
        conn.execute("COMMIT")
        logger.info("material_order_inserted", order_id=order_id, job_id=job_id)
    except Exception as e:
        logger.error("material_order_insert_failed", job_id=job_id, error=str(e))
        raise
    finally:
        conn.close()

def insert_schedule(job_id: str, crew_name: str, install_date: str, delivery_date: str, status: str) -> None:
    """Insert a production schedule and generate a UUID for the record.

    Args:
        job_id (str): The unique identifier for the job.
        crew_name (str): The assigned installation crew.
        install_date (str): The scheduled installation date.
        delivery_date (str): The scheduled material delivery date.
        status (str): The current schedule status.
        
    Raises:
        Exception: If the database insertion fails.
    """
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        schedule_id = str(uuid.uuid4())
        conn.execute('''
            INSERT OR REPLACE INTO schedule (id, job_id, crew_name, install_date, delivery_date, status)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (schedule_id, job_id, crew_name, install_date, delivery_date, status))
        conn.execute("COMMIT")
        logger.info("schedule_inserted", schedule_id=schedule_id, job_id=job_id)
    except Exception as e:
        logger.error("schedule_insert_failed", job_id=job_id, error=str(e))
        raise
    finally:
        conn.close()


def standardize_vault_filename(job_id: str, filename: str, category: str = "UNSPECIFIED", file_type: str = "", photo_index: int | None = None) -> str:
    """
    Format raw filenames into clean, standardized, professional titles for the Document Vault.
    Converts generic names like 'inspection_report_homeowner.pdf' or 'IMG_20260721_182205.jpg'
    into structured names like 'Young_520_Fontaine_Dr_Inspection_Report.pdf' or 'Inspection_Photo_01.jpg'.
    """
    if not filename:
        return filename
    
    ext = Path(filename).suffix.lower()
    base_name = Path(filename).stem

    # Fetch Homeowner & Street details from DB if available
    prefix = ""
    try:
        conn = get_connection()
        row = conn.execute("SELECT homeowner_name, address_line1 FROM jobs WHERE id = ?", (job_id,)).fetchone()
        conn.close()
        if row:
            last_name = ""
            if row["homeowner_name"]:
                parts = row["homeowner_name"].strip().split()
                last_name = parts[-1] if parts else ""
            
            street = ""
            if row["address_line1"]:
                street = row["address_line1"].strip().replace(".", "").replace(",", "")
                street_parts = street.split()[:3]
                street = "_".join(street_parts)
            
            if last_name and street:
                prefix = f"{last_name}_{street}_"
            elif last_name:
                prefix = f"{last_name}_"
    except Exception:
        pass

    cat_upper = (category or "").upper()
    ft_upper = (file_type or "").upper()
    fn_lower = filename.lower()

    # Standardize PDF Documents
    if ext == ".pdf":
        if "EVIDENCE" in cat_upper or "EVIDENCE" in fn_lower or "grid" in fn_lower or "evidence_grid" in fn_lower:
            return f"{prefix}Inspection_Evidence_Grid{ext}"
        elif "HOMEOWNER" in cat_upper or "HOMEOWNER" in ft_upper or "homeowner" in fn_lower or "inspection_report_homeowner" in fn_lower or "INSPECTION_REPORT" in cat_upper or "inspection_report" in fn_lower:
            return f"{prefix}Homeowner_Inspection_Report{ext}"
        elif "CONTINGENCY" in cat_upper or "contingency" in fn_lower:
            return f"{prefix}Contingency_Agreement{ext}"
        elif "RETAIL" in cat_upper or "retail" in fn_lower:
            return f"{prefix}Retail_Contract{ext}"
        elif "SUPPLEMENT" in cat_upper or "supplement" in fn_lower:
            return f"{prefix}Supplement_Request{ext}"
        elif "INVOICE" in cat_upper or "invoice" in fn_lower:
            return f"{prefix}Invoice{ext}"
        elif "MATERIAL" in cat_upper or "po" in fn_lower or "material_order" in fn_lower:
            return f"{prefix}Material_Order{ext}"
        elif "CANCELLATION" in cat_upper or "cancellation" in fn_lower:
            return f"{prefix}Notice_of_Cancellation{ext}"
        elif "EAGLEVIEW" in cat_upper or "hover" in cat_upper or "measurement" in cat_upper:
            return f"{prefix}Measurement_Report{ext}"
        elif "STATEMENT_OF_LOSS" in cat_upper or "sol" in fn_lower:
            return f"{prefix}Statement_of_Loss{ext}"

    # Standardize Image / Photo Files
    if ext in [".jpg", ".jpeg", ".png", ".heic", ".webp"] or "PHOTO" in cat_upper:
        if photo_index is not None:
            return f"Inspection_Photo_{photo_index:02d}{ext}"
        
        # If already formatted as Inspection_Photo_XX.jpg, preserve it
        if base_name.startswith("Inspection_Photo_") or base_name.startswith("Photo_"):
            return filename

        try:
            conn = get_connection()
            count_row = conn.execute(
                "SELECT COUNT(*) as c FROM job_documents WHERE job_id = ? AND (category IN ('INSPECTION_PHOTO', 'PHOTO') OR file_type LIKE 'image/%')",
                (job_id,)
            ).fetchone()
            conn.close()
            idx = (count_row["c"] + 1) if count_row else 1
            return f"Inspection_Photo_{idx:02d}{ext}"
        except Exception:
            return f"Inspection_Photo_{base_name[-4:]}{ext}"

    return filename


def standardize_existing_job_documents(job_id: str | None = None) -> None:
    """
    Update existing records in job_documents to use clean, standardized filenames.
    """
    conn = get_connection()
    try:
        if job_id:
            docs = conn.execute("SELECT id, job_id, filename, file_type, category FROM job_documents WHERE job_id = ? ORDER BY created_at ASC, rowid ASC", (job_id,)).fetchall()
        else:
            docs = conn.execute("SELECT id, job_id, filename, file_type, category FROM job_documents ORDER BY created_at ASC, rowid ASC").fetchall()
        
        photo_counters: dict[str, int] = {}
        for d in docs:
            j_id = d["job_id"]
            cat = (d["category"] or "").upper()
            ext = Path(d["filename"]).suffix.lower()
            
            p_idx = None
            if ext in [".jpg", ".jpeg", ".png", ".heic", ".webp"] or "PHOTO" in cat:
                photo_counters[j_id] = photo_counters.get(j_id, 0) + 1
                p_idx = photo_counters[j_id]

            new_fn = standardize_vault_filename(j_id, d["filename"], d["category"], d["file_type"], photo_index=p_idx)
            if new_fn != d["filename"]:
                conn.execute("UPDATE job_documents SET filename = ? WHERE id = ?", (new_fn, d["id"]))
        conn.commit()
    except Exception as err:
        logger.warning("standardize_existing_docs_failed", error=str(err))
    finally:
        conn.close()


def insert_job_document(job_id: str, filename: str, file_type: str, storage_path: str, sha256_hash: str | None = None, visibility: str = "office_only", category: str = "UNSPECIFIED", replace_existing: bool = False) -> str:
    """Register a generated or uploaded file in the universal document vault."""
    filename = standardize_vault_filename(job_id, filename, category, file_type)
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        if replace_existing:
            if category and category != "UNSPECIFIED":
                conn.execute("DELETE FROM job_documents WHERE job_id = ? AND (filename = ? OR category = ?)", (job_id, filename, category))
            else:
                conn.execute("DELETE FROM job_documents WHERE job_id = ? AND filename = ?", (job_id, filename))
        else:
            # Exact Deduplication Check:
            # 1. If sha256_hash is provided, match identical content for this job
            if sha256_hash:
                cursor = conn.execute(
                    "SELECT id FROM job_documents WHERE job_id = ? AND sha256_hash = ?",
                    (job_id, sha256_hash)
                )
                row = cursor.fetchone()
                if row:
                    conn.execute(
                        "UPDATE job_documents SET filename = ?, file_type = ?, storage_path = ?, visibility = ?, category = ? WHERE id = ?",
                        (filename, file_type, storage_path, visibility, category, row["id"])
                    )
                    conn.execute("COMMIT")
                    return row["id"]
            else:
                # 2. If sha256_hash is absent, match by exact job_id, filename, category & storage_path
                cursor = conn.execute(
                    "SELECT id FROM job_documents WHERE job_id = ? AND filename = ? AND category = ? AND storage_path = ?",
                    (job_id, filename, category, storage_path)
                )
                row = cursor.fetchone()
                if row:
                    conn.execute("COMMIT")
                    return row["id"]

        doc_id = str(uuid.uuid4())
        conn.execute(
            """INSERT INTO job_documents (id, job_id, filename, file_type, storage_path, sha256_hash, visibility, category) 
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (doc_id, job_id, filename, file_type, storage_path, sha256_hash, visibility, category)
        )
        conn.execute("COMMIT")
        logger.info("job_document_registered", doc_id=doc_id, job_id=job_id, filename=filename, file_type=file_type, visibility=visibility, category=category)
        return doc_id
    except Exception as e:
        logger.error("job_document_registration_failed", error=str(e))
        raise
    finally:
        conn.close()

def get_job_documents(job_id: str, file_type: str | None = None) -> list[dict]:
    """Return all document versions for a job, newest first.
    
    This is the canonical read path. Because documents are append-only,
    the most recent row for a given filename is the authoritative version.
    Pass file_type to filter (e.g., 'SUPPLEMENT_PDF', 'EAGLEVIEW_PDF').
    """
    conn = get_connection()
    try:
        if file_type:
            cursor = conn.execute(
                """SELECT * FROM job_documents 
                   WHERE job_id = ? AND file_type = ?
                   ORDER BY created_at DESC""",
                (job_id, file_type)
            )
        else:
            cursor = conn.execute(
                """SELECT * FROM job_documents 
                   WHERE job_id = ? 
                   ORDER BY created_at DESC""",
                (job_id,)
            )
        return [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()

def get_job_document_by_hash(job_id: str, sha256_hash: str) -> dict | None:
    """Lookup an existing document by its content hash to prevent duplicate processing."""
    conn = get_connection()
    try:
        cursor = conn.execute(
            "SELECT * FROM job_documents WHERE job_id = ? AND sha256_hash = ?",
            (job_id, sha256_hash)
        )
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def get_financials(job_id: str) -> dict | None:
    """Fetch the raw financial parameters for a given job."""
    conn = get_connection()
    try:
        cursor = conn.execute("""
            SELECT job_id, 
                   revenue_cents,
                   carrier_rcv_cents,
                   material_cost_cents,
                   labor_cost_cents,
                   overhead_pct,
                   canvasser_commission_pct,
                   permits_fee_cents,
                   deductible_cents,
                   acv_payment_cents,
                   recoverable_depreciation_cents,
                   depreciation_cents,
                   net_claim_cents,
                   carrier_initial_rcv_cents,
                   carrier_supplemented_rcv_cents,
                   qbo_exported,
                   qbo_exported_at,
                   deductible_paid,
                   deductible_paid_cents
            FROM financials 
            WHERE job_id = ?
        """, (job_id,))
        row = cursor.fetchone()
        return dict(row) if row else None
    except Exception as e:
        logger.error("get_financials_failed", error=str(e))
        raise
    finally:
        conn.close()

def get_monthly_financials(month: int, year: int) -> list[dict]:
    """Aggregate all INVOICED or CLOSED jobs for a specific month and year."""
    conn = get_connection()
    try:
        cursor = conn.execute("""
            SELECT j.id, j.homeowner_name, j.status, 
                   f.revenue_cents, 
                   f.material_cost_cents, 
                   f.labor_cost_cents, 
                   f.overhead_pct, 
                   f.canvasser_commission_pct, 
                   f.permits_fee_cents
            FROM jobs j
            JOIN financials f ON j.id = f.job_id
            WHERE j.status IN ('INVOICED', 'CLOSED')
            AND cast(strftime('%m', j.created_at) as integer) = ?
            AND cast(strftime('%Y', j.created_at) as integer) = ?
        """, (month, year))
        return [dict(r) for r in cursor]
    except Exception as e:
        logger.error("get_monthly_financials_failed", error=str(e))
        return []
    finally:
        conn.close()

def transition_material_flags(
    job_id: str,
    materials_ordered: bool | None = None,
    materials_on_site: bool | None = None,
) -> None:
    """
    Restricted toggle for operational material confirmation flags.
    Called exclusively by PATCH /api/operations/job/{id}/materials.

    If materials_on_site transitions to True, this function ALSO
    calls _update_job_status_internal() to advance the job to MATERIALS_ON_SITE,
    making it eligible for crew scheduling.

    If materials_on_site transitions to False (rollback), the job
    status is reverted to MATERIAL_ORDERED.

    Raises ValueError if job_id not found.
    """
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            "SELECT id FROM jobs WHERE id = ?", (job_id,)
        )
        if not cursor.fetchone():
            raise ValueError(f"Job {job_id} not found.")

        if materials_ordered is not None:
            conn.execute(
                "UPDATE jobs SET materials_ordered = ? WHERE id = ?",
                (1 if materials_ordered else 0, job_id),
            )
        if materials_on_site is not None:
            conn.execute(
                "UPDATE jobs SET materials_on_site = ? WHERE id = ?",
                (1 if materials_on_site else 0, job_id),
            )

        # Drive the state machine from the flag transitions atomically
        if materials_on_site is True:
            _update_job_status_internal(
                conn,
                job_id,
                JobStatus.MATERIALS_ON_SITE,
                "Materials confirmed on-site via Operations Board toggle.",
            )
        elif materials_on_site is False:
            _update_job_status_internal(
                conn,
                job_id,
                JobStatus.MATERIAL_ORDERED,
                "Materials on-site confirmation rolled back via Operations Board.",
            )

        conn.execute("COMMIT")
        logger.info(
            "material_flags_updated",
            job_id=job_id,
            ordered=materials_ordered,
            on_site=materials_on_site,
        )
    except Exception as e:
        conn.execute("ROLLBACK")
        logger.error("material_flags_update_failed", job_id=job_id, error=str(e))
        raise
    finally:
        conn.close()


def get_qbo_export_batch() -> list[dict]:
    """
    Returns all jobs eligible for QBO batch export:
    status IN (SUPPLEMENT_APPROVED, INVOICED) AND qbo_exported = 0.
    Joins jobs + financials. Returns empty list if none pending.
    """
    conn = get_connection()
    try:
        cursor = conn.execute(
            """
            SELECT j.id as job_id, j.homeowner_name, j.status,
                   f.revenue_cents, 
                   f.carrier_rcv_cents, 
                   f.material_cost_cents,
                   f.labor_cost_cents, 
                   f.overhead_pct,
                   f.canvasser_commission_pct, 
                   f.permits_fee_cents
            FROM jobs j
            JOIN financials f ON j.id = f.job_id
            WHERE j.status IN ('SUPPLEMENT_APPROVED', 'INVOICED')
              AND f.qbo_exported = 0
            ORDER BY j.created_at ASC
            """
        )
        return [dict(r) for r in cursor.fetchall()]
    except Exception as e:
        logger.error("get_qbo_export_batch_failed", error=str(e))
        return []
    finally:
        conn.close()


def mark_qbo_exported(job_ids: list[str]) -> None:
    """
    Idempotency lock: mark a batch of jobs as QBO-exported.
    Sets qbo_exported=1 and qbo_exported_at=NOW for each job_id.
    Safe to call multiple times — subsequent calls are no-ops due
    to the qbo_exported=0 filter in get_qbo_export_batch().
    """
    if not job_ids:
        return
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.executemany(
            """UPDATE financials
               SET qbo_exported = 1,
                   qbo_exported_at = CURRENT_TIMESTAMP
               WHERE job_id = ?""",
            [(jid,) for jid in job_ids],
        )
        conn.execute("COMMIT")
        logger.info("qbo_batch_marked_exported", count=len(job_ids))
    except Exception as e:
        logger.error("qbo_mark_exported_failed", error=str(e))
        raise
    finally:
        conn.close()

def update_job_metadata(job_id: str, inspector_name: str, inspection_date: str, inspection_notes: str) -> None:
    """Update inspection-related metadata for a specific job."""
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute('''
            UPDATE jobs 
            SET inspector_name = ?, inspection_date = ?, inspection_notes = ? 
            WHERE id = ?
        ''', (inspector_name, inspection_date, inspection_notes, job_id))
        conn.execute("COMMIT")
        logger.info("job_metadata_updated", job_id=job_id)
    except Exception as e:
        logger.error("update_job_metadata_failed", job_id=job_id, error=str(e))
        raise
    finally:
        conn.close()

def log_ai_usage(job_id: str | None, tokens_used: int, model_name: str, operation_type: str) -> None:
    """Synchronously log AI token consumption to the database."""
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        log_id = str(uuid.uuid4())
        conn.execute('''
            INSERT INTO ai_usage_logs (id, job_id, tokens_used, model_name, operation_type)
            VALUES (?, ?, ?, ?, ?)
        ''', (log_id, job_id, tokens_used, model_name, operation_type))
        conn.execute("COMMIT")
    except Exception as e:
        logger.error("failed_to_log_ai_usage", error=str(e))
    finally:
        conn.close()

def atomic_qbo_export() -> list[dict]:
    """
    Atomically fetch all QBO-eligible jobs and mark them exported
    in a single IMMEDIATE transaction, preventing double-export
    race conditions from concurrent requests.

    Returns the batch rows (as dicts) that were locked.
    Returns empty list if nothing pending.
    """
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute("""
            SELECT j.id as job_id, j.invoice_id, j.homeowner_name, j.status,
                   j.claim_number,
                   f.revenue_cents, 
                   f.carrier_rcv_cents, 
                   f.material_cost_cents,
                   f.labor_cost_cents, 
                   f.overhead_pct,
                   f.canvasser_commission_pct, 
                   f.permits_fee_cents
            FROM jobs j
            JOIN financials f ON j.id = f.job_id
            WHERE j.status IN ('SUPPLEMENT_APPROVED', 'INVOICED')
              AND f.qbo_exported = 0
            ORDER BY j.created_at ASC
        """)
        batch = [dict(r) for r in cursor.fetchall()]
        if batch:
            job_ids = [r["job_id"] for r in batch]
            conn.executemany(
                """UPDATE financials
                   SET qbo_exported = 1,
                       qbo_exported_at = CURRENT_TIMESTAMP
                   WHERE job_id = ?""",
                [(jid,) for jid in job_ids],
            )
        conn.execute("COMMIT")
        logger.info("atomic_qbo_export_complete", count=len(batch))
        return batch
    except Exception as e:
        conn.execute("ROLLBACK")
        logger.error("atomic_qbo_export_failed", error=str(e))
        raise
    finally:
        conn.close()

def mark_supplement_sent(job_id: str) -> None:
    """
    Transitions job from SUPPLEMENT_GENERATED/SUPPLEMENT_SUBMITTED to
    AWAITING_CARRIER_RESPONSE and records the sent timestamp.
    Idempotent — safe to call multiple times.
    """
    conn = get_connection()
    try:
        cursor = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,))
        row = cursor.fetchone()
        if row and row["status"] in ("SUPPLEMENT_GENERATED", "SUPPLEMENT_SUBMITTED"):
            conn.execute("BEGIN IMMEDIATE")
            _update_job_status_internal(conn, job_id, "AWAITING_CARRIER_RESPONSE", "Supplement marked as sent to carrier.")
            conn.execute("UPDATE jobs SET supplement_sent_at = CURRENT_TIMESTAMP WHERE id = ?", (job_id,))
            conn.commit()
            logger.info("supplement_sent_and_status_updated", job_id=job_id)
    except Exception as e:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        logger.error("mark_supplement_sent_failed", job_id=job_id, error=str(e))
        raise
    finally:
        conn.close()

def record_financial_payment(
    job_id: str,
    payment_type: str,  # 'acv', 'depreciation', 'retail', 'deductible'
    amount: float | None = None,  # in dollars, optional
    date_received: str | None = None,  # date string, optional
    deductible_paid: bool | None = None
) -> None:
    """
    Records granular payments (ACV, Depreciation, Retail) or deductible status
    on the financials table and triggers the corresponding status transition.
    """
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        # Ensure financials row exists
        row = conn.execute("SELECT job_id FROM financials WHERE job_id = ?", (job_id,)).fetchone()
        if not row:
            conn.execute("INSERT INTO financials (job_id, revenue_cents, carrier_rcv_cents, material_cost_cents, labor_cost_cents, overhead_pct, canvasser_commission_pct) VALUES (?, 0, 0, 0, 0, 0.0, 0.0)", (job_id,))

        amount_cents = int(round(amount * 100)) if amount is not None else None
        
        status_to_transition = None
        note = ""

        if payment_type == "acv":
            if date_received:
                conn.execute(
                    "UPDATE financials SET acv_payment_received_at = ? WHERE job_id = ?",
                    (date_received, job_id)
                )
            else:
                conn.execute(
                    "UPDATE financials SET acv_payment_received_at = CURRENT_TIMESTAMP WHERE job_id = ?",
                    (job_id,)
                )
            if amount_cents is not None:
                conn.execute(
                    "UPDATE jobs SET acv_received = 1, acv_received_at = CURRENT_TIMESTAMP, acv_check_amount_cents = ?, acv_check_date = ? WHERE id = ?",
                    (amount_cents, date_received or __import__('datetime').date.today().isoformat(), job_id)
                )
            status_to_transition = "ACV_PAYMENT_RECEIVED"
            note = f"ACV payment recorded: ${amount or 0.0:.2f}"

        elif payment_type == "depreciation":
            if date_received:
                conn.execute(
                    "UPDATE financials SET depreciation_payment_received_at = ? WHERE job_id = ?",
                    (date_received, job_id)
                )
            else:
                conn.execute(
                    "UPDATE financials SET depreciation_payment_received_at = CURRENT_TIMESTAMP WHERE job_id = ?",
                    (job_id,)
                )
            if amount_cents is not None:
                conn.execute(
                    "UPDATE jobs SET supplement_received = 1, supplement_received_at = CURRENT_TIMESTAMP, supplement_check_amount_cents = ?, supplement_check_date = ? WHERE id = ?",
                    (amount_cents, date_received or __import__('datetime').date.today().isoformat(), job_id)
                )
            status_to_transition = "DEPRECIATION_PAYMENT_RECEIVED"
            note = f"Depreciation payment recorded: ${amount or 0.0:.2f}"

        elif payment_type == "retail":
            if date_received:
                conn.execute(
                    "UPDATE financials SET retail_payment_received_at = ? WHERE job_id = ?",
                    (date_received, job_id)
                )
            else:
                conn.execute(
                    "UPDATE financials SET retail_payment_received_at = CURRENT_TIMESTAMP WHERE job_id = ?",
                    (job_id,)
                )
            status_to_transition = "RETAIL_PAYMENT_RECEIVED"
            note = f"Retail payment recorded: ${amount or 0.0:.2f}"

        elif payment_type == "deductible":
            dp_val = 1 if deductible_paid else 0
            if amount_cents is not None:
                conn.execute(
                    "UPDATE financials SET deductible_paid = ?, deductible_paid_cents = ? WHERE job_id = ?",
                    (dp_val, amount_cents, job_id)
                )
            else:
                conn.execute(
                    "UPDATE financials SET deductible_paid = ? WHERE job_id = ?",
                    (dp_val, job_id)
                )
            conn.execute(
                "UPDATE jobs SET deductible_paid_cents = ? WHERE id = ?",
                (amount_cents or 0, job_id)
            )

        if payment_type in ("acv", "depreciation"):
            fin_row = conn.execute("SELECT acv_payment_received_at, depreciation_payment_received_at FROM financials WHERE job_id = ?", (job_id,)).fetchone()
            if fin_row and fin_row["acv_payment_received_at"] and fin_row["depreciation_payment_received_at"]:
                status_to_transition = "PAYMENT_RECEIVED"
                note = "Both ACV and Depreciation payments received."

        if status_to_transition:
            _update_job_status_internal(conn, job_id, status_to_transition, note)

        conn.commit()
        logger.info("recorded_financial_payment", job_id=job_id, payment_type=payment_type)
    except Exception as e:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        logger.error("recorded_financial_payment_failed", job_id=job_id, error=str(e))
        raise
    finally:
        conn.close()

def toggle_payment_flag(job_id: str, flag: str, amount: float | None = None, date_received: str | None = None) -> dict:
    """
    Toggles acv_received or supplement_received for a job.
    Returns the new state. flag must be one of the two allowed
    values — hard-coded whitelist, no dynamic SQL construction.
    """
    allowed = {"acv_received", "supplement_received"}
    if flag not in allowed:
        raise ValueError(f"Invalid flag: {flag}")

    ts_col = flag + "_at"
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            f"SELECT {flag} FROM jobs WHERE id = ?", (job_id,)
        )
        row = cursor.fetchone()
        if not row:
            raise ValueError(f"Job {job_id} not found.")

        # If amount is provided, we are capturing a check. This forces it to ON.
        if amount is not None and date_received is not None:
            new_val = 1
            amount_cents = int(round(amount * 100))
            if flag == "acv_received":
                conn.execute(
                    "UPDATE jobs SET acv_received=1, acv_received_at=CURRENT_TIMESTAMP, acv_check_amount_cents=?, acv_check_date=? WHERE id=?",
                    (amount_cents, date_received, job_id)
                )
            else:
                conn.execute(
                    "UPDATE jobs SET supplement_received=1, supplement_received_at=CURRENT_TIMESTAMP, supplement_check_amount_cents=?, supplement_check_date=? WHERE id=?",
                    (amount_cents, date_received, job_id)
                )
        else:
            new_val = 0 if row[flag] else 1
            ts_val = "CURRENT_TIMESTAMP" if new_val else "NULL"
            conn.execute(
                f"""UPDATE jobs
                    SET {flag} = ?,
                        {ts_col} = {ts_val}
                    WHERE id = ?""",
                (new_val, job_id)
            )
        conn.commit()
        row2 = conn.execute(
            "SELECT acv_received, supplement_received "
            "FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        both_received = bool(
            row2
            and row2["acv_received"] == 1
            and row2["supplement_received"] == 1
        )
        return {
            "flag": flag,
            "new_value": new_val,
            "job_id": job_id,
            "commission_triggered": both_received
        }
    finally:
        conn.close()

def generate_invoice_id() -> str:
    """
    Atomically generate the next sequential invoice ID.
    Format: WR-YY-NNNN (e.g., WR-26-0001).
    Uses a single-row counter table to prevent race conditions.
    Safe under concurrent BEGIN IMMEDIATE transactions.
    """
    from datetime import datetime as _dt
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE invoice_sequence SET last_seq = last_seq + 1 "
            "WHERE id = 1"
        )
        row = conn.execute(
            "SELECT last_seq FROM invoice_sequence WHERE id = 1"
        ).fetchone()
        seq = row["last_seq"]
        year_short = _dt.now(__import__('datetime').timezone.utc).strftime("%y")
        invoice_id = f"WR-{year_short}-{seq:04d}"
        conn.execute("COMMIT")
        logger.info("invoice_id_generated", invoice_id=invoice_id)
        return invoice_id
    except Exception as e:
        conn.execute("ROLLBACK")
        logger.error("invoice_id_generation_failed", error=str(e))
        raise
    finally:
        conn.close()

def get_aging_jobs() -> list[dict]:
    """
    Returns ONLY jobs in AWAITING_CARRIER_RESPONSE where
    the number of days since supplement_sent_at is
    greater than or equal to carrier_sla_days.
    All filtering is done in SQL - callers get only
    genuinely overdue jobs.
    """
    conn = get_connection()
    try:
        cursor = conn.execute("""
            SELECT id as job_id, invoice_id,
                   homeowner_name,
                   supplement_sent_at,
                   escalation_sent_at,
                   carrier_sla_days,
                   CAST(
                       julianday('now') -
                       julianday(supplement_sent_at)
                   AS INTEGER) AS days_waiting
            FROM jobs
            WHERE status = 'AWAITING_CARRIER_RESPONSE'
              AND supplement_sent_at IS NOT NULL
              AND CAST(
                      julianday('now') -
                      julianday(supplement_sent_at)
                  AS INTEGER) >= carrier_sla_days
            ORDER BY supplement_sent_at ASC
        """)
        return [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()

# ============================================================
# FIELD REP CRUD — Phase 9
# ============================================================

def create_field_rep(name: str, pin: str) -> dict:
    """
    Create a new field rep. Raises ValueError if the PIN is already in
    use by another rep OR if the PIN conflicts with any static system
    PIN (admin_pin, accounting_pin, operations_pin) in config.py.
    PIN must be exactly 4 digits.
    Returns the created rep as a dict.
    """
    if not pin.isdigit() or len(pin) != 4:
        raise ValueError("PIN must be exactly 4 digits.")
    settings = get_settings()
    reserved = {
        settings.admin_pin,
        settings.accounting_pin,
        settings.operations_pin,
    }
    if pin in reserved:
        raise ValueError("PIN conflicts with a reserved system PIN.")
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        hashed_pin = pwd_context.hash(pin)
        rep_id = str(uuid.uuid4())
        conn.execute(
            """INSERT INTO field_reps
               (id, name, pin_hash, is_active)
               VALUES (?, ?, ?, 1)""",
            (rep_id, name.strip(), hashed_pin)
        )
        conn.execute("COMMIT")
        logger.info("field_rep_created", rep_id=rep_id, name=name)
        return {"id": rep_id, "name": name.strip(), "is_active": 1}
    except sqlite3.IntegrityError:
        conn.execute("ROLLBACK")
        raise ValueError("PIN is already in use.")
    finally:
        conn.close()


def list_field_reps(include_inactive: bool = False) -> list[dict]:
    """Return all field reps, active only by default."""
    conn = get_connection()
    try:
        if include_inactive:
            cursor = conn.execute(
                "SELECT * FROM field_reps "
                "ORDER BY name ASC"
            )
        else:
            cursor = conn.execute(
                "SELECT * FROM field_reps "
                "WHERE is_active = 1 "
                "ORDER BY name ASC"
            )
        return [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()


def get_field_rep_by_pin(pin: str) -> dict | None:
    """
    Look up an active field rep by their plaintext PIN using bcrypt verify.
    Returns None if not found, if rep is inactive,
    or if the field_reps table does not yet exist.
    """
    conn = get_connection()
    try:
        # We must retrieve all active reps and verify against their hashes
        # Since field reps are typically few (e.g. <50), this is acceptable.
        rows = conn.execute(
            "SELECT * FROM field_reps WHERE is_active = 1"
        ).fetchall()
        for row in rows:
            if pwd_context.verify(pin, row["pin_hash"]):
                return dict(row)
        return None
    except sqlite3.OperationalError:
        # Table may not exist yet (first-run before init_db completes)
        return None
    finally:
        conn.close()


def update_field_rep(
    rep_id: str,
    name: str | None = None,
    pin: str | None = None,
    is_active: bool | None = None,
) -> dict:
    """
    Update a field rep's name, PIN, and/or active status.
    PIN uniqueness and system-PIN conflict checks apply.
    Returns the updated rep dict.
    Raises ValueError if rep_id not found.
    """
    if pin is not None:
        if not pin.isdigit() or len(pin) != 4:
            raise ValueError("PIN must be exactly 4 digits.")
        settings = get_settings()
        reserved = {
            settings.admin_pin,
            settings.accounting_pin,
            settings.operations_pin,
        }
        if pin in reserved:
            raise ValueError("PIN conflicts with a reserved system PIN.")
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM field_reps WHERE id = ?",
            (rep_id,)
        ).fetchone()
        if not row:
            conn.execute("ROLLBACK")
            raise ValueError(f"Rep {rep_id} not found.")
        new_name   = name      if name      is not None else row["name"]
        new_pin_hash = pwd_context.hash(pin) if pin is not None else row["pin_hash"]
        new_active = (1 if is_active else 0) \
                     if is_active is not None \
                     else row["is_active"]
        conn.execute(
            """UPDATE field_reps
               SET name = ?, pin_hash = ?,
                   is_active = ?,
                   updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (new_name, new_pin_hash, new_active, rep_id)
        )
        conn.execute("COMMIT")
        return {"id": rep_id, "name": new_name, "is_active": new_active}
    except sqlite3.IntegrityError:
        conn.execute("ROLLBACK")
        raise ValueError("PIN is already in use.")
    finally:
        conn.close()


def update_job_claim_info(
    job_id: str,
    claim_number: str | None = None,
    insurer_name: str | None = None,
    loss_date: str | None = None,
    policy_number: str | None = None,
    adjuster_name: str | None = None,
    adjuster_phone: str | None = None,
    adjuster_email: str | None = None,
    ice_barrier_required: bool | None = None,
) -> dict:
    """
    Update insurance claim metadata on a job record.
    Supports updates at any point by office staff or field reps.
    """
    import uuid
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        updates: dict[str, str | int | None] = {}
        if claim_number is not None:
            updates["claim_number"] = claim_number.strip()
        if insurer_name is not None:
            updates["insurer_name"] = insurer_name.strip()
        if policy_number is not None:
            updates["policy_type"] = policy_number.strip()
        if adjuster_name is not None:
            updates["adjuster_name"] = adjuster_name.strip()
        if adjuster_phone is not None:
            updates["adjuster_phone"] = adjuster_phone.strip()
        if adjuster_email is not None:
            updates["adjuster_email"] = adjuster_email.strip()
        if loss_date is not None:
            updates["loss_date"] = loss_date.strip() if loss_date else None
        if ice_barrier_required is not None:
            updates["ice_barrier_required"] = 1 if ice_barrier_required else 0

        if updates:
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            values = list(updates.values()) + [job_id]
            cursor = conn.execute(f"UPDATE jobs SET {set_clause} WHERE id = ?", values)
            if cursor.rowcount == 0:
                raise ValueError("Job not found")

        if loss_date and loss_date.strip():
            cursor = conn.execute("SELECT id FROM storm_verifications WHERE job_id = ?", (job_id,))
            row = cursor.fetchone()
            if row:
                conn.execute("UPDATE storm_verifications SET loss_date = ? WHERE job_id = ?", (loss_date.strip(), job_id))
            else:
                sv_id = str(uuid.uuid4())
                conn.execute('''
                    INSERT INTO storm_verifications (id, job_id, loss_date, event_type, begin_lat, begin_lon, match_confidence)
                    VALUES (?, ?, ?, 'Unknown', 0.0, 0.0, 'Pending')
                ''', (sv_id, job_id, loss_date.strip()))

        conn.execute("COMMIT")
        return {"status": "success", "job_id": job_id}
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def update_shingle_info(
    job_id: str,
    shingle_color: str | None = None,
    shingle_type: str | None = None,
) -> dict:
    """Update shingle color and type on a job record."""
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        updates = {}
        if shingle_color is not None:
            updates["shingle_color"] = shingle_color.strip() if shingle_color else None
        if shingle_type is not None:
            updates["shingle_type"] = shingle_type.strip() if shingle_type else None

        if updates:
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            values = list(updates.values()) + [job_id]
            cursor = conn.execute(f"UPDATE jobs SET {set_clause} WHERE id = ?", values)
            if cursor.rowcount == 0:
                raise ValueError("Job not found")
        conn.execute("COMMIT")
        return {"status": "success", "job_id": job_id}
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def get_job_schedule(job_id: str) -> dict | None:
    """Fetch the production schedule for a given job."""
    conn = get_connection()
    try:
        cursor = conn.execute("SELECT * FROM schedule WHERE job_id = ?", (job_id,))
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_completed_jobs() -> list[dict]:
    """Fetch all jobs that are in the CLOSED status."""
    conn = get_connection()
    try:
        cursor = conn.execute("SELECT * FROM jobs WHERE status = 'CLOSED' ORDER BY created_at DESC")
        return [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()

