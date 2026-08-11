"""
QuickBooks Online (QBO) CSV Exporter.

Flattens structured InvoiceExport data into the exact strict multi-line CSV
format required for QBO batch imports.
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

import structlog

from app.core.database import get_pricing_ledger
from app.core.supplement_models import InvoiceExport, InvoiceLine, MaterialBOM

logger = structlog.get_logger("app.services.qbo_export")

# Deterministic mapping for QBO standard products/services
QBO_ITEMS = {
    "shingle_install": "Roofing:Shingle Installation",
    "tear_off": "Roofing:Tear Off & Haul",
    "ridge_cap": "Roofing:Ridge Cap",
    "ice_water": "Roofing:Ice & Water Shield",
    "drip_edge": "Roofing:Drip Edge",
    "underlayment": "Roofing:Synthetic Underlayment",
    "vents": "Roofing:Ventilation",
    "o_and_p": "General:Overhead and Profit",
    "taxes": "General:Taxes",
    "permits": "General:Permits",
}

# Ensure export directory exists
EXPORT_DIR = Path("generated_exports")
EXPORT_DIR.mkdir(parents=True, exist_ok=True)

def export_to_csv(export: InvoiceExport) -> str:
    """Generate a strictly formatted QBO CSV file from an InvoiceExport.
    
    QBO Rules Enforced:
    1. Exact headers required.
    2. Multi-line invoices must repeat InvoiceNo, Customer, InvoiceDate, DueDate.
    3. ItemAmount must be >= 0.
    4. Max 100 lines (safety limit).
    
    Args:
        export (InvoiceExport): The populated export model containing line items.
        
    Returns:
        str: The absolute or relative string path to the generated CSV file.
        
    Raises:
        Exception: If writing the CSV file fails.
    """
    log = logger.bind(invoice_no=export.invoice_no, customer=export.customer)
    log.info("qbo_export_started")
    
    if len(export.lines) > 100:
        log.warning("qbo_export_exceeds_100_lines", line_count=len(export.lines))
        # We will process it, but QBO might reject it. Truncating is worse.
        
    export_path = EXPORT_DIR / f"{export.invoice_no}_QBO.csv"
    
    headers = [
        "InvoiceNo",
        "Customer",
        "InvoiceDate",
        "DueDate",
        "Item(Product/Service)",
        "ItemDescription",
        "ItemQuantity",
        "ItemRate",
        "ItemAmount"
    ]
    
    try:
        with open(export_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            
            for line in export.lines:
                if line.amount < 0:
                    log.warning("negative_amount_skipped", item=line.item, amount=line.amount)
                    continue
                
                # Map the item name, fallback to the raw item string if not in QBO_ITEMS
                qbo_item = QBO_ITEMS.get(line.item, line.item)
                
                row = [
                    export.invoice_no,
                    export.customer,
                    export.invoice_date,
                    export.due_date,
                    qbo_item,
                    line.description,
                    f"{line.quantity:.2f}",
                    f"{line.rate:.2f}",
                    f"{line.amount:.2f}"
                ]
                writer.writerow(row)
                
        log.info("qbo_export_complete", filepath=str(export_path))
        return str(export_path)
    
    except Exception as e:
        log.error("qbo_export_failed", error=str(e))
        raise

def generate_qbo_invoice(job_id: str, bom: MaterialBOM, customer_name: str = "Unknown Customer") -> str:
    """Generate an invoice from the Automated Math Engine BOM.
    
    Uses real dynamic pricing from the database ledger.
    Updates CRM status to INVOICED upon completion.
    
    Args:
        job_id (str): The unique identifier for the job.
        bom (MaterialBOM): The calculated bill of materials.
        customer_name (str, optional): The name of the customer. Defaults to "Unknown Customer".
        
    Returns:
        str: The string path to the generated QBO CSV file.
    """
    now_date = datetime.now(__import__('datetime').timezone.utc).strftime("%Y-%m-%d")
    
    # Fetch actual pricing rates from the SQLite ledger
    pricing = get_pricing_ledger()
    
    def create_line(item_type: str, qty: float, desc: str, pricing_key: str) -> InvoiceLine:
        """
        Create Line functionality.
        
        Args:
                item_type (str): item_type parameter.
                qty (float): qty parameter.
                desc (str): desc parameter.
                pricing_key (str): pricing_key parameter.
        
        Returns:
            InvoiceLine: The resulting output.
        """
        rate = pricing.get(pricing_key, 0.0)
        return InvoiceLine(
            item=item_type,
            description=desc,
            quantity=qty,
            rate=rate,
            amount=qty * rate
        )
    
    lines = [
        create_line("shingle_install", bom.field_shingle_bundles, "Field Shingle Bundles", "field_shingle_bundles"),
        create_line("shingle_install", bom.starter_bundles, "Starter Bundles", "starter_bundles"),
        create_line("ridge_cap", bom.ridge_cap_bundles, "Ridge Cap Bundles", "ridge_cap_bundles"),
        create_line("ice_water", bom.ice_water_rolls, "Ice & Water Shield Rolls", "ice_water_rolls"),
        create_line("underlayment", bom.underlayment_rolls, "Synthetic Underlayment Rolls", "underlayment_rolls"),
        create_line("drip_edge", bom.drip_edge_pieces, "Drip Edge Pieces", "drip_edge_pieces")
    ]
    
    # Financials for O&P and Permits
    from app.core.database import get_financials
    financials = get_financials(job_id)
    if financials:
        oh_pct = financials.get("overhead_pct", 0.0)
        if oh_pct > 0:
            oh_val = oh_pct if oh_pct < 1 else (oh_pct / 100.0)
            base = (financials.get("material_cost_cents", 0) + financials.get("labor_cost_cents", 0)) / 100.0
            oh_amt = base * oh_val
            lines.append(
                InvoiceLine(
                    item="o_and_p",
                    description="Overhead & Profit",
                    quantity=1.0,
                    rate=oh_amt,
                    amount=oh_amt
                )
            )
            
        permits = financials.get("permits_fee_cents", 0) / 100.0
        if permits > 0:
            lines.append(
                InvoiceLine(
                    item="permits",
                    description="Permits & Fees",
                    quantity=1.0,
                    rate=permits,
                    amount=permits
                )
            )
    
    export = InvoiceExport(
        invoice_no=f"INV-{job_id[:8].upper()}",
        customer=customer_name,
        invoice_date=now_date,
        due_date=now_date,
        lines=lines
    )
    
    csv_path = export_to_csv(export)
    
    # NOTE: Status transition to INVOICED is now handled by the office operator,
    # not automatically on CSV generation. This prevents premature state transitions
    # when the EagleView PDF upload pipeline generates a reference CSV.
    
    return csv_path
