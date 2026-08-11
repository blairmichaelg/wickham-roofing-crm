from app.services.pdf.commission import CommissionGenerator
from app.services.pdf.documents import DocumentsGenerator
from app.services.pdf.inspection_report import (
    InspectionReportGenerator,  # exported for direct use; NOT in PDFGenerator MRO
)
from app.services.pdf.invoice import InvoiceGenerator
from app.services.pdf.supplement import SupplementGenerator


class PDFGenerator(InvoiceGenerator, SupplementGenerator, CommissionGenerator, DocumentsGenerator):
    """
    Composite PDF generator for all office/field documents that operate on job dicts.
    InspectionReportGenerator is intentionally excluded — it operates on InspectionJob
    Pydantic models, not raw job dicts, and is instantiated directly in inspection_processor.py.
    """
