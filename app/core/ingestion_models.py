from decimal import ROUND_HALF_UP, Decimal
from typing import Generic, TypeVar

from pydantic import BaseModel, Field, model_validator

T = TypeVar('T')

class EvidenceRef(BaseModel):
    """EvidenceRef definition."""
    doc_id: str
    page: int
    bounding_box: str | None = None
    raw_text: str
    extraction_method: str

class SourcedValue(BaseModel, Generic[T]):
    """SourcedValue definition."""
    value: T
    evidence: list[EvidenceRef] = Field(default_factory=list)
    verified: bool = False

class ClaimLineItem(BaseModel):
    """ClaimLineItem definition."""
    category_code: str
    activity_code: str
    description: str
    quantity: SourcedValue[Decimal]
    unit: SourcedValue[str]
    unit_price: SourcedValue[Decimal]
    tax: SourcedValue[Decimal]
    claimed_rcv: SourcedValue[Decimal]
    depreciation: SourcedValue[Decimal]
    acv: SourcedValue[Decimal]
    verified: bool = False

    @model_validator(mode='after')
    def validate_math(self) -> 'ClaimLineItem':
        # (quantity * unit_price) + tax == claimed_rcv (tolerance 0.02)
        """
        Validate Math functionality.
        
        Returns:
            'ClaimLineItem': The resulting output.
        """
        q = self.quantity.value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        up = self.unit_price.value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        t = self.tax.value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        rcv = self.claimed_rcv.value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        
        calculated = (q * up) + t
        difference = abs(calculated - rcv)
        
        if difference <= Decimal("0.02"):
            self.verified = True
        else:
            self.verified = False
            
        return self

class RoofGeometry(BaseModel):
    """RoofGeometry definition."""
    pitch: SourcedValue[str]
    total_squares: SourcedValue[Decimal]
    eaves_lf: SourcedValue[Decimal]
    valleys_lf: SourcedValue[Decimal]
    rakes_lf: SourcedValue[Decimal]

class ClaimFinancials(BaseModel):
    """ClaimFinancials definition."""
    gross_rcv: SourcedValue[Decimal]
    total_depreciation: SourcedValue[Decimal]
    deductible: SourcedValue[Decimal]
    net_claim: SourcedValue[Decimal]

class UniversalClaimAST(BaseModel):
    """UniversalClaimAST definition."""
    line_items: list[ClaimLineItem]
    roof_geometry: RoofGeometry
    financials: ClaimFinancials
    claim_number: SourcedValue[str] | None = None
    insurer_name: SourcedValue[str] | None = None
    shingle_type: str | None = None
    shingle_color: str | None = None
    source_doc_sha256: str = Field(
        description="SHA256 hash of the source PDF that produced this AST. "
                    "Written at API boundary, passed through ARQ worker payload, "
                    "printed in supplement PDF footer for legal provenance."
    )
    source_doc_id: str = Field(
        description="job_documents.id FK of the source PDF row. "
                    "Enables direct DB lookup of the originating file."
    )
    ast_version: int = Field(
        default=1,
        description="Monotonically incrementing version. Increment on each "
                    "re-ingestion of a revised SoL to maintain append-only ledger."
    )

    @model_validator(mode='after')
    def validate_total(self) -> 'UniversalClaimAST':
        """
        Validate Total functionality.
        
        Returns:
            'UniversalClaimAST': The resulting output.
        """
        total_rcv = sum((item.claimed_rcv.value for item in self.line_items), Decimal("0.00"))
        
        # Simple cross-check. If they mismatch significantly, we could flag it.
        # For now, we rely on the line-item level verification.
        if abs(total_rcv - self.financials.gross_rcv.value) <= Decimal("0.05"):
            self.financials.gross_rcv.verified = True
            
        return self
