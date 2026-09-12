"""
Billing and accounting endpoints for Office Control Center.
"""

import asyncio
import csv
import io
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import structlog
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Body,
    Depends,
    HTTPException,
    Request,
)
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from app.api.auth import verify_accounting, verify_admin
from app.core.backup import backup_database
from app.core.database import (
    JobStatus,
    atomic_qbo_export,
    get_connection,
    get_financials,
    update_job_status,
    upsert_financials,
)
from app.core.job_costing import compute_job_profitability
from app.core.utils import now_utc
from app.services.pdf import PDFGenerator

logger = structlog.get_logger("app.api.office.billing")
router = APIRouter()

class FinancialsPayload(BaseModel):
    """FinancialsPayload definition with strict currency coercion and cents calculation."""
    revenue: float
    carrier_rcv: float
    materials: float
    labor: float
    deductible: float = 0.0
    acv_payment: float = 0.0
    recoverable_depreciation: float = 0.0
    overhead_pct: float = 0.25
    commission_pct: float = 0.10
    permits_fee: float = 0.0

    @field_validator(
        "revenue",
        "carrier_rcv",
        "materials",
        "labor",
        "deductible",
        "acv_payment",
        "recoverable_depreciation",
        "permits_fee",
        mode="before",
    )
    @classmethod
    def coerce_currency_amount(cls, v: Any) -> float:
        """Coerce and validate string/float/int input to rounded dollar cents."""
        if v is None:
            return 0.0
        if isinstance(v, str):
            v = v.replace("$", "").replace(",", "").strip()
        try:
            val = float(v)
        except (ValueError, TypeError):
            raise ValueError(f"Invalid currency value: {v}")
        if val < 0:
            raise ValueError(f"Currency amount cannot be negative: {val}")
        return round(val, 2)

    @property
    def revenue_cents(self) -> int:
        return int(round(self.revenue * 100))

    @property
    def carrier_rcv_cents(self) -> int:
        return int(round(self.carrier_rcv * 100))

    @property
    def materials_cents(self) -> int:
        return int(round(self.materials * 100))

    @property
    def labor_cents(self) -> int:
        return int(round(self.labor * 100))

    @property
    def deductible_cents(self) -> int:
        return int(round(self.deductible * 100))

    @property
    def acv_payment_cents(self) -> int:
        return int(round(self.acv_payment * 100))

    @property
    def recoverable_depreciation_cents(self) -> int:
        return int(round(self.recoverable_depreciation * 100))

    @property
    def permits_fee_cents(self) -> int:
        return int(round(self.permits_fee * 100))


@router.get("/jobs/{job_id}/qbo_export", dependencies=[Depends(verify_admin)])
def download_qbo_export(job_id: str):
    """Returns the generated QBO CSV for the given job."""
    csv_path = EXPORT_DIR / f"INV-{job_id[:8].upper()}_QBO.csv"
    
    if not csv_path.exists():
        raise HTTPException(status_code=404, detail="QBO Export not found for this job.")
        
    return FileResponse(
        path=csv_path,
        filename=f"INV-{job_id[:8].upper()}_QBO.csv",
        media_type="text/csv"
    )


def _sync_update_job_financials(job_id: str, payload: FinancialsPayload):
    conn = get_connection()
    try:
        cursor = conn.execute("SELECT commission_pct_override FROM jobs WHERE id = ?", (job_id,))
        job_row = cursor.fetchone()
        override = job_row["commission_pct_override"] if job_row else None
    finally:
        conn.close()

    # Calculate precise financials
    results = compute_job_profitability(
        revenue_cents=int(round(payload.revenue * 100)),
        materials_cents=int(round(payload.materials * 100)),
        labor_cents=int(round(payload.labor * 100)),
        overhead_pct=payload.overhead_pct,
        commission_pct=payload.commission_pct,
        commission_pct_override=override
    )
    
    # Directive 4: Low Margin Alert
    if results["gross_margin"] < 0.35:
        logger.warning(
            "low_margin_alert", 
            job_id=job_id, 
            gross_margin=results["gross_margin"],
            revenue=payload.revenue,
            direct_costs=results["direct_costs_cents"] / 100.0
        )
        
    # Store raw parameters in DB
    upsert_financials(
        job_id=job_id,
        revenue_cents=int(round(payload.revenue * 100)),
        carrier_rcv_cents=int(round(payload.carrier_rcv * 100)),
        material_cost_cents=int(round(payload.materials * 100)),
        labor_cost_cents=int(round(payload.labor * 100)),
        overhead_pct=payload.overhead_pct,
        canvasser_commission_pct=payload.commission_pct,
        permits_fee_cents=int(round(payload.permits_fee * 100)),
        deductible_cents=int(round(payload.deductible * 100)),
        acv_payment_cents=int(round(payload.acv_payment * 100)),
        recoverable_depreciation_cents=int(round(payload.recoverable_depreciation * 100)),
    )
    
    # Convert returned integer cents back to dollars for API response
    return {
        "direct_costs": results["direct_costs_cents"] / 100.0,
        "gross_profit": results["gross_profit_cents"] / 100.0,
        "gross_margin": results["gross_margin"],
        "overhead_cost": results["overhead_cost_cents"] / 100.0,
        "net_profit": results["net_profit_cents"] / 100.0,
        "canvasser_commission": results["canvasser_commission_cents"] / 100.0,
        "effective_commission_pct": results["effective_commission_pct"]
    }


@router.post("/jobs/{job_id}/financials", dependencies=[Depends(verify_accounting)])
async def update_job_financials(job_id: str, payload: FinancialsPayload, bg_tasks: BackgroundTasks):
    """
    Process pre-build job costing parameters from the Office Dashboard.
    Calculates exact margin profiles and logs alerts if profitability is too low.
    """
    try:
        results = await asyncio.to_thread(_sync_update_job_financials, job_id, payload)
        
        # Trigger Hot Backup
        bg_tasks.add_task(backup_database)
        
        return {"status": "success", "financials": results}
    except Exception as e:
        logger.error("job_costing_failed", job_id=job_id, error=str(e))
        raise HTTPException(status_code=500, detail="Failed to calculate and save financials.")


class AccountingBrief(BaseModel):
    """AccountingBrief definition."""
    supplemented_rcv_added: str
    qbo_ready_count: int
    rows: list[dict[str, Any]]


@router.get("/accounting/brief", response_model=AccountingBrief, dependencies=[Depends(verify_accounting)])
def get_accounting_brief():
    """Zero-click read projection for accounting dashboard."""
    conn = get_connection()
    try:
        # Sum RCV for all active pipeline jobs (non-CLOSED)
        cursor = conn.execute("""
            SELECT COALESCE(SUM(f.carrier_rcv_cents), 0) as total_rcv_cents
            FROM financials f
            JOIN jobs j ON j.id = f.job_id
            WHERE j.status IN (
                'SUPPLEMENT_GENERATED', 'SUPPLEMENT_SUBMITTED', 'AWAITING_CARRIER_RESPONSE',
                'SUPPLEMENT_APPROVED', 'SUPPLEMENT_DENIED', 'MATERIAL_ORDERED', 'MATERIALS_ON_SITE',
                'INSTALL_SCHEDULED', 'INSTALL_COMPLETED', 'FINAL_INSPECTION', 'INSPECTION_COMPLETED',
                'FINAL_INSPECTION_COMPLETED', 'INVOICED', 'PAYMENT_RECEIVED',
                'EV_ORDERED', 'ACV_PAYMENT_RECEIVED', 'DEPRECIATION_PAYMENT_RECEIVED', 'RETAIL_PAYMENT_RECEIVED'
            )
        """)
        rcv_row = cursor.fetchone()
        supplemented_rcv = f"${(rcv_row['total_rcv_cents'] / 100.0):,.2f}"
        
        # Get count of jobs awaiting QBO export
        cursor = conn.execute("""
            SELECT COUNT(*) as cnt
            FROM jobs j
            JOIN financials f ON j.id = f.job_id
            WHERE j.status IN ('SUPPLEMENT_APPROVED', 'INVOICED', 'ACV_PAYMENT_RECEIVED', 'DEPRECIATION_PAYMENT_RECEIVED', 'RETAIL_PAYMENT_RECEIVED')
              AND f.qbo_exported = 0
        """)
        qbo_ready = cursor.fetchone()["cnt"]

        # Fetch active pipeline jobs
        cursor = conn.execute("""
            SELECT j.id, j.invoice_id, j.homeowner_name, j.status, j.job_type,
                   j.acv_received, j.acv_received_at,
                   j.supplement_received, j.supplement_received_at,
                   f.carrier_rcv_cents, f.recoverable_depreciation_cents,
                   f.qbo_exported, f.acv_payment_received_at, f.depreciation_payment_received_at,
                   f.retail_payment_received_at, f.deductible_paid, f.deductible_paid_cents, f.deductible_cents,
                   COALESCE(f.last_payment_received_at, j.last_payment_received_at) AS last_payment_received_at
            FROM jobs j
            LEFT JOIN financials f ON j.id = f.job_id
            WHERE j.status IN (
                'SUPPLEMENT_GENERATED', 'SUPPLEMENT_SUBMITTED', 'AWAITING_CARRIER_RESPONSE',
                'SUPPLEMENT_APPROVED', 'SUPPLEMENT_DENIED', 'MATERIAL_ORDERED', 'MATERIALS_ON_SITE',
                'INSTALL_SCHEDULED', 'INSTALL_COMPLETED', 'FINAL_INSPECTION', 'INSPECTION_COMPLETED',
                'FINAL_INSPECTION_COMPLETED', 'INVOICED', 'PAYMENT_RECEIVED',
                'EV_ORDERED', 'ACV_PAYMENT_RECEIVED', 'DEPRECIATION_PAYMENT_RECEIVED', 'RETAIL_PAYMENT_RECEIVED'
            )
            ORDER BY j.created_at ASC
        """)
        active_rows = cursor.fetchall()
        
        # Fetch last 5 completed jobs
        cursor = conn.execute("""
            SELECT j.id, j.invoice_id, j.homeowner_name, j.status, j.job_type,
                   j.acv_received, j.acv_received_at,
                   j.supplement_received, j.supplement_received_at,
                   f.carrier_rcv_cents, f.recoverable_depreciation_cents,
                   f.qbo_exported, f.acv_payment_received_at, f.depreciation_payment_received_at,
                   f.retail_payment_received_at, f.deductible_paid, f.deductible_paid_cents, f.deductible_cents,
                   COALESCE(f.last_payment_received_at, j.last_payment_received_at) AS last_payment_received_at
            FROM jobs j
            LEFT JOIN financials f ON j.id = f.job_id
            WHERE j.status = 'CLOSED'
            ORDER BY j.created_at DESC
            LIMIT 5
        """)
        closed_rows = cursor.fetchall()
        
        rows = list(active_rows) + list(closed_rows)
        
        acct_rows = []
        for r in rows:
            recoverable_dep = (r["recoverable_depreciation_cents"] or 0) / 100.0
            carrier_rcv = (r["carrier_rcv_cents"] or 0) / 100.0
            if recoverable_dep and recoverable_dep > 0:
                acv_expected = carrier_rcv - recoverable_dep
                supp_expected = recoverable_dep
            else:
                acv_expected = None
                supp_expected = None
            
            acct_rows.append({
                "job_id": r["id"],
                "invoice_id": r["invoice_id"],
                "name": r["homeowner_name"],
                "status": r["status"],
                "job_type": r["job_type"] or "insurance",
                "acv_received": r["acv_received"],
                "acv_received_at": r["acv_received_at"],
                "supplement_received": r["supplement_received"],
                "supplement_received_at": r["supplement_received_at"],
                "acv_expected": acv_expected,
                "supp_expected": supp_expected,
                "carrier_rcv": carrier_rcv,
                "qbo_exported": bool(r["qbo_exported"]) if r["qbo_exported"] is not None else False,
                "acv_payment_received_at": r["acv_payment_received_at"],
                "depreciation_payment_received_at": r["depreciation_payment_received_at"],
                "retail_payment_received_at": r["retail_payment_received_at"],
                "last_payment_received_at": r["last_payment_received_at"],
                "deductible_paid": bool(r["deductible_paid"]) if r["deductible_paid"] is not None else False,
                "deductible_paid_cents": r["deductible_paid_cents"] or 0,
                "deductible_cents": r["deductible_cents"] or 0
            })
        
        return AccountingBrief(
            supplemented_rcv_added=supplemented_rcv,
            qbo_ready_count=qbo_ready,
            rows=acct_rows
        )
    finally:
        conn.close()


@router.get("/accounting/qbo-export", dependencies=[Depends(verify_accounting)])
async def export_qbo_csv(token=Depends(verify_accounting)):
    """
    Batch QBO export. Queries all eligible jobs (qbo_exported=0),
    generates CSV, sets idempotency lock, returns file download.
    Returns 204 with message if no jobs are pending export.
    """
    batch = atomic_qbo_export()
    if not batch:
        from fastapi import Response
        return Response(
            content="No jobs pending QBO export.",
            status_code=204
        )

    import datetime
    today_dt = now_utc()
    today_str = today_dt.strftime("%Y-%m-%d")
    due_date_str = (today_dt + datetime.timedelta(days=30)).strftime("%Y-%m-%d")

    output = io.StringIO()
    fieldnames = [
        "*Customer",
        "*InvoiceDate",
        "*DueDate",
        "Terms",
        "Item(Product/Service)",
        "ItemQuantity",
        "ItemRate",
        "ItemAmount",
        "Memo"
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for job in batch:
        qbo_row = {
            "*Customer":             job["homeowner_name"],
            "*InvoiceDate":          today_str,
            "*DueDate":              due_date_str,
            "Terms":                 "Net 30",
            "Item(Product/Service)": "Roofing Services",
            "ItemQuantity":          1,
            "ItemRate":              job["carrier_rcv_cents"] / 100.0,
            "ItemAmount":            job["carrier_rcv_cents"] / 100.0,
            "Memo":                  f"Invoice {job.get('invoice_id','N/A')} | "
                                     f"Claim {job.get('claim_number','N/A')}"
        }
        writer.writerow(qbo_row)

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition":
                "attachment; filename=wickham_qbo_export.csv"
        }
    )


@router.get("/accounting/commissions-ready", response_class=JSONResponse, dependencies=[Depends(verify_accounting)])
def get_commissions_ready():
    """
    Get Commissions Ready functionality.
    
    Returns:
        Any: The resulting output.
    """
    conn = get_connection()
    try:
        cursor = conn.execute("""
            SELECT j.id as job_id, j.invoice_id, j.homeowner_name, j.canvasser_name, j.commission_generated_at,
                   j.commission_pct_override, f.revenue_cents, f.canvasser_commission_pct
            FROM jobs j
            LEFT JOIN financials f ON j.id = f.job_id
            WHERE j.commission_ready = 1
            ORDER BY j.commission_generated_at DESC
        """)
        results = []
        for r in cursor.fetchall():
            row = dict(r)
            effective_pct = row["commission_pct_override"] if row["commission_pct_override"] is not None else row["canvasser_commission_pct"]
            if effective_pct is None: 
                effective_pct = 0.10
            revenue = (row["revenue_cents"] or 0) / 100.0
            row["canvasser_commission"] = revenue * effective_pct
            results.append(row)
        return results
    finally:
        conn.close()


@router.get("/jobs/{job_id}/docs/commission", response_class=FileResponse, dependencies=[Depends(verify_accounting)])
def download_commission(job_id: str):
    """
    Download Commission functionality.
    
    Args:
            job_id (str): job_id parameter.
    
    Returns:
        Any: The resulting output.
    """
    try:
        job_id = str(uuid.UUID(job_id))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job_id format.")
    from app.core.database import get_job_documents
    docs = get_job_documents(job_id, file_type="COMMISSION_PDF")
    if not docs:
        raise HTTPException(404, "No commission statement found.")
    path = docs[0]["storage_path"]
    if not Path(path).exists():
        raise HTTPException(404, "Commission PDF missing.")
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=f"Commission_Statement_{job_id[:8]}.pdf"
    )


class TogglePaymentPayload(BaseModel):
    flag: Literal["acv_received", "supplement_received"]
    amount: float | None = Field(None, ge=0.0, le=1_000_000.0)
    date_received: str | None = None

    @field_validator("date_received")
    @classmethod
    def validate_date_received(cls, v: str | None) -> str | None:
        if v is not None and v != "":
            try:
                datetime.fromisoformat(v)
            except ValueError:
                raise ValueError("date_received must be a valid ISO date string (e.g. YYYY-MM-DD)")
        return v


@router.post("/accounting/jobs/{job_id}/toggle-payment", dependencies=[Depends(verify_accounting)])
async def toggle_payment_route(job_id: str, payload: TogglePaymentPayload, request: Request):
    from app.core.database import advance_status_for_payment, toggle_payment_flag
    try:
        result = toggle_payment_flag(job_id, payload.flag, payload.amount, payload.date_received)
        if result.get("commission_triggered"):
            # Promote status safely via canonical advance_status_for_payment
            conn = get_connection()
            try:
                payment_type = "acv" if payload.flag == "acv_received" else "depreciation"
                advance_status_for_payment(conn, job_id, payment_type, amount=payload.amount)
                conn.commit()
            finally:
                conn.close()
            await request.app.state.redis_pool.enqueue_job(
                "process_commission",
                job_id=job_id
            )
        return {"status": "success"}
    except Exception as e:
        logger.error("toggle_payment_failed", error=str(e))
        raise HTTPException(status_code=400, detail=str(e))


class MarkPaymentPayload(BaseModel):
    payment_type: Literal["acv", "depreciation", "retail", "deductible"]
    amount: float | None = Field(None, ge=0.0, le=1_000_000.0)
    date_received: str | None = None
    deductible_paid: bool | None = None

    @field_validator("date_received")
    @classmethod
    def validate_date_received(cls, v: str | None) -> str | None:
        if v is not None and v != "":
            try:
                datetime.fromisoformat(v)
            except ValueError:
                raise ValueError("date_received must be a valid ISO date string (e.g. YYYY-MM-DD)")
        return v


@router.post("/accounting/jobs/{job_id}/mark-payment", dependencies=[Depends(verify_accounting)])
async def mark_payment_route(job_id: str, payload: MarkPaymentPayload, request: Request):
    from app.core.database import record_financial_payment
    try:
        await asyncio.to_thread(
            record_financial_payment,
            job_id,
            payload.payment_type,
            payload.amount,
            payload.date_received,
            payload.deductible_paid
        )
        # Trigger commission job if both ACV and Depreciation payments are received
        conn = get_connection()
        try:
            fin_row = conn.execute(
                "SELECT acv_payment_received_at, depreciation_payment_received_at FROM financials WHERE job_id = ?",
                (job_id,)
            ).fetchone()
            if fin_row and fin_row["acv_payment_received_at"] and fin_row["depreciation_payment_received_at"]:
                await request.app.state.redis_pool.enqueue_job(
                    "process_commission",
                    job_id=job_id
                )
        finally:
            conn.close()
        return {"status": "success"}
    except Exception as e:
        logger.error("mark_payment_failed", error=str(e))
        raise HTTPException(status_code=400, detail=str(e))


class CommissionOverridePayload(BaseModel):
    commission_pct: float | None


@router.post("/accounting/jobs/{job_id}/commission-override", dependencies=[Depends(verify_accounting)])
def commission_override_route(job_id: str, payload: CommissionOverridePayload):
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute("UPDATE jobs SET commission_pct_override = ? WHERE id = ?", (payload.commission_pct, job_id))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Job not found")
        conn.execute("COMMIT")
        return {"status": "success"}
    except HTTPException:
        conn.execute("ROLLBACK")
        raise
    except Exception as e:
        conn.execute("ROLLBACK")
        logger.error("commission_override_failed", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to save commission override")
    finally:
        conn.close()


@router.post("/accounting/jobs/{job_id}/invoice", dependencies=[Depends(verify_accounting)])
async def create_invoice_route(job_id: str, bg_tasks: BackgroundTasks, is_emergency: bool = False):
    """
    Transition a job to INVOICED status, generating an invoice number and
    making it visible in the QBO export queue.
    Enforces Georgia FBPA 5-business-day post-denial invoicing lock (O.C.G.A. § 10-1-393.12).
    """
    try:
        job_id = str(uuid.UUID(job_id))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job_id format.")

    from app.services.compliance import is_post_denial_invoicing_locked

    locked, lock_msg, _ = is_post_denial_invoicing_locked(job_id, is_emergency=is_emergency)
    if locked:
        raise HTTPException(
            status_code=400,
            detail=lock_msg,
        )

    conn = get_connection()
    try:
        row = conn.execute("SELECT status, invoice_id FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Job not found.")
        
        current_status = row["status"]
        valid_for_invoice = [
            "SUPPLEMENT_APPROVED", "SCOPE_APPROVED", "MATERIAL_ORDERED",
            "MATERIALS_ON_SITE", "INSTALL_SCHEDULED", "INSTALL_COMPLETED",
            "FINAL_INSPECTION", "INSPECTION_COMPLETED", "FINAL_INSPECTION_COMPLETED",
            "SUPPLEMENT_GENERATED"
        ]
        if current_status not in valid_for_invoice:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot invoice from status '{current_status}'. Job must be at least SUPPLEMENT_APPROVED or INSTALL_COMPLETED."
            )
    finally:
        conn.close()

    try:
        await asyncio.to_thread(update_job_status, job_id, "INVOICED", "Invoice created from Accounting Dashboard")
        bg_tasks.add_task(backup_database)
        return {"status": "success", "message": "Job transitioned to INVOICED status."}
    except Exception as e:
        logger.error("invoice_creation_failed", job_id=job_id, error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to create invoice: {e!s}")


@router.patch("/accounting/jobs/{job_id}/commission/paid", dependencies=[Depends(verify_accounting)])
async def mark_commission_paid(job_id: str, bg_tasks: BackgroundTasks):
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute("UPDATE jobs SET commission_ready = 0 WHERE id = ?", (job_id,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Job not found")
        conn.execute("COMMIT")
    except HTTPException:
        conn.execute("ROLLBACK")
        raise
    except Exception as e:
        conn.execute("ROLLBACK")
        logger.error("mark_commission_paid_failed", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to mark commission paid")
    finally:
        conn.close()

    try:
        conn = get_connection()
        row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
        conn.close()
        if row and row["status"] == "PAYMENT_RECEIVED":
            await asyncio.to_thread(update_job_status, job_id, "CLOSED", "Commission paid. Job closed/archived.")
        bg_tasks.add_task(backup_database)
        return {"status": "success"}
    except Exception as e:
        logger.error("mark_commission_paid_status_failed", error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to transition job status: {e!s}")

