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
        """
        Validate the arithmetic of the line item.

        Verifies that (quantity * unit_price) + tax matches claimed_rcv
        within a tolerance of 0.02. Sets the `verified` flag accordingly.

        Returns:
            ClaimLineItem: The validated line item instance.
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
        Validate overall carrier financial metrics and mathematical consistency.

        Performs three deterministic checks:
        1. Cross-checks sum of line items RCV against gross RCV.
        2. Validates overall claim financials equation: gross_rcv - depreciation - deductible == net_claim.
        3. Validates each line item's RCV minus depreciation matches ACV.
        4. Ensures all physical roof geometry values are non-negative.

        Returns:
            UniversalClaimAST: The validated instance.

        Raises:
            ValueError: If any mathematical constraint or bounds check fails.
        """
        # 1. Cross-check total line items RCV against gross RCV
        total_rcv = sum((item.claimed_rcv.value for item in self.line_items), Decimal("0.00"))
        if abs(total_rcv - self.financials.gross_rcv.value) <= Decimal("0.05"):
            self.financials.gross_rcv.verified = True
        
        # 2. Enforce strict overall claim financials math: gross_rcv - total_depreciation - deductible == net_claim
        gross = self.financials.gross_rcv.value
        dep = self.financials.total_depreciation.value
        ded = self.financials.deductible.value
        net = self.financials.net_claim.value
        
        expected_net = gross - dep - ded
        if abs(expected_net - net) <= Decimal("0.05"):
            self.financials.net_claim.verified = True
        else:
            self.financials.net_claim.verified = False
            raise ValueError(
                f"Overall claim financials mismatch: gross_rcv ({gross}) - total_depreciation ({dep}) - deductible ({ded}) "
                f"= expected net claim ({expected_net}), but net_claim was extracted as {net}."
            )
            
        # 3. Enforce strict line item math: claimed_rcv - depreciation == acv
        for item in self.line_items:
            item_rcv = item.claimed_rcv.value
            item_dep = item.depreciation.value
            item_acv = item.acv.value
            expected_acv = item_rcv - item_dep
            if abs(expected_acv - item_acv) <= Decimal("0.05"):
                item.verified = True
            else:
                item.verified = False
                raise ValueError(
                    f"Line item '{item.description}' arithmetic mismatch: claimed_rcv ({item_rcv}) "
                    f"- depreciation ({item_dep}) = expected acv ({expected_acv}), "
                    f"but acv was extracted as {item_acv}."
                )
                
        # 4. Validate non-negative geometry measurements
        geom = self.roof_geometry
        for field_name in ["total_squares", "eaves_lf", "valleys_lf", "rakes_lf"]:
            field_val = getattr(geom, field_name).value
            if field_val < Decimal("0.00"):
                raise ValueError(f"Roof geometry field '{field_name}' cannot be negative: {field_val}")
            
        return self
