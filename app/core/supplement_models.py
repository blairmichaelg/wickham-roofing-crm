"""
Pydantic V2 models for the InsurTech Supplement Engine.

These models enforce strict data contracts between the extraction,
reconciliation, and generation layers. The LLM never touches these
models for math — only for extraction and narrative.

Key design decisions:
- EagleViewData uses @computed_field for automatic waste normalization
- LineItem allows None for missing quantities (SoLs are inconsistent)
- DiscrepancyReport is the pure-Python math engine's output contract
"""

from typing import Literal

from pydantic import BaseModel


class MaterialBOM(BaseModel):
    """
    Deterministic Material Bill of Materials.
    Calculated purely from mathematical coverage constants and EagleView metrics.
    """
    field_shingle_bundles: int
    starter_bundles: int
    ridge_cap_bundles: int
    ice_water_rolls: int
    underlayment_rolls: int
    drip_edge_pieces: int


class EagleViewData(BaseModel):
    """
    Normalized EagleView measurement data extracted via pdfplumber.

    The @computed_field automatically converts raw SF to roofing Squares
    with the configured waste factor applied, so downstream consumers
    never need to do this math themselves.
    """

    total_area_sf: float
    rake_lf: float
    valley_lf: float
    ridge_lf: float
    hip_lf: float
    eaves_lf: float
    drip_edge_lf: float
    flashing_lf: float | None = None
    step_flashing_lf: float | None = None
    flashing_wall_lf: float | None = None
    pipe_boot_count: int | None = None
    vent_count: int | None = None
    starter_strip_lf: float | None = None
    total_facets: int
    predominant_pitch: str

    @property
    def total_squares(self) -> float:
        return round(self.total_area_sf / 100.0, 2)


class LineItem(BaseModel):
    """
    A single line item from a Carrier Statement of Loss / Xactimate estimate.

    Fields may be None when the carrier omits data or the value
    could not be confidently extracted. The LLM is instructed to
    return null rather than guess.
    """

    trade: str
    code: str
    description: str
    quantity: float | None = None
    unit_of_measure: str | None = None
    unit_price: float | None = None
    waste_percent_included: float | None = None
    
    # Forensic/AST fields
    tax: float | None = None
    claimed_rcv: float | None = None
    depreciation: float | None = None
    acv: float | None = None
    page: int | None = None


class StatementOfLoss(BaseModel):
    """
    Structured representation of a Carrier Statement of Loss PDF,
    extracted via Gemini Multimodal File API.
    """

    source_system: Literal["xactimate", "symbility", "unknown"] = "unknown"
    carrier_name: str | None = None
    claim_number: str | None = None
    line_items: list[LineItem] = []
    overhead_and_profit_included: bool | None = None

    # Geometry fields
    pitch: str | None = None
    total_squares: float | None = None
    eaves_lf: float | None = None
    valleys_lf: float | None = None
    rakes_lf: float | None = None

    # Shingle material fields (from SoL line items / materials section)
    shingle_type: str | None = None
    shingle_color: str | None = None

    # Financial fields
    gross_rcv: float | None = None
    total_depreciation: float | None = None
    deductible: float | None = None
    net_claim: float | None = None



class Discrepancy(BaseModel):
    """A single variance identified between EagleView and SoL data."""

    category: str
    description: str
    ev_value: float | None = None
    sol_value: float | None = None
    variance: float | None = None
    code_citation: str | None = None
    xactimate_code: str | None = None


class DiscrepancyReport(BaseModel):
    """
    Output of the deterministic Python reconciliation engine.

    This is the bridge between the math layer and the AI narrative layer.
    The AI uses this report to write the supplement letter, but
    NEVER recalculates any of the numbers.
    """

    job_id: str
    ev_normalized_squares: float
    sol_total_rfg_squares: float
    square_variance: float
    waste_explanation: str
    material_bom: MaterialBOM
    discrepancies: list[Discrepancy] = []
    defensive_narrative: str | None = None
    total_facets: int = 0
    predominant_pitch: str = ""
    valley_lf: float = 0.0


class CodeSection(BaseModel):
    """
    Structured representation of a building code or amendment.
    Used for strict citation adherence in AI generation.
    """
    code_set: str          # e.g., "IRC"
    edition: str           # e.g., "2024"
    jurisdiction: str      # e.g., "GA", "National"
    section: str           # e.g., "R905.2.8.5"
    text: str


class InvoiceLine(BaseModel):
    """A single line item mapped to a QuickBooks Online product/service."""
    item: str
    description: str
    quantity: float
    rate: float
    amount: float


class InvoiceExport(BaseModel):
    """
    Structured representation of an invoice to be exported to QuickBooks Online via CSV.
    """
    invoice_no: str
    customer: str
    invoice_date: str
    due_date: str
    lines: list[InvoiceLine]
