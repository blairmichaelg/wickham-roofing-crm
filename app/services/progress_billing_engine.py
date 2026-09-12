"""Progress Billing & Retainage Calculation Engine.

Deterministic, pure-Python domain logic for multi-stage commercial roofing contracts,
Schedule of Values (SOV) tracking, retainage holdbacks, and progress applications.
Zero AI involvement; completely reproducible and testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field, field_validator


class SOVItem(BaseModel):
    """Line item in a commercial contract Schedule of Values."""
    id: str
    item_code: str
    description: str
    scheduled_value_cents: int = Field(gt=0, description="Scheduled value in integer cents")

    @field_validator("scheduled_value_cents", mode="before")
    @classmethod
    def coerce_cents(cls, v: Any) -> int:
        if isinstance(v, str):
            v = v.replace("$", "").replace(",", "").strip()
            v = int(round(float(v) * 100))
        elif isinstance(v, float):
            v = int(round(v * 100))
        return int(v)


class ProgressItemInput(BaseModel):
    """Progress reported for a single SOV line item in a billing cycle."""
    schedule_item_id: str
    work_completed_cents: int = Field(ge=0, default=0)
    stored_materials_cents: int = Field(ge=0, default=0)

    @field_validator("work_completed_cents", "stored_materials_cents", mode="before")
    @classmethod
    def coerce_cents(cls, v: Any) -> int:
        if isinstance(v, str):
            v = v.replace("$", "").replace(",", "").strip()
            v = int(round(float(v) * 100))
        elif isinstance(v, float):
            v = int(round(v * 100))
        return int(v)


class ComputedProgressItem(BaseModel):
    """Calculated status of an SOV line item for a billing cycle."""
    schedule_item_id: str
    item_code: str
    description: str
    scheduled_value_cents: int
    previous_completed_cents: int
    current_work_completed_cents: int
    current_stored_materials_cents: int
    total_completed_to_date_cents: int
    percentage_complete: float
    balance_to_finish_cents: int


class ComputedProgressApplication(BaseModel):
    """Complete computed progress billing application."""
    application_no: int
    retainage_percent: float
    items: list[ComputedProgressItem]
    total_scheduled_value_cents: int
    total_completed_to_date_cents: int
    previous_certificates_cents: int
    current_payment_due_gross_cents: int
    retainage_withheld_this_period_cents: int
    retainage_released_this_period_cents: int
    total_retainage_held_cents: int
    net_payment_due_cents: int
    overall_percent_complete: float


def compute_progress_billing(
    schedule: list[SOVItem],
    line_inputs: list[ProgressItemInput],
    application_no: int,
    retainage_percent: float,
    previous_applications: list[ComputedProgressApplication] | None = None,
    retainage_release_cents: int = 0,
) -> ComputedProgressApplication:
    """Compute progress billing application with exact integer cents and retainage holdback.
    
    Business Rules Enforced:
    1. Retainage percent is strictly configured per contract / job (not hardcoded to statutory maximums).
    2. Cumulative billing cannot exceed 100% of the Schedule of Values across all line items.
    3. Retainage released cannot exceed total retainage previously withheld + currently withheld.
    
    Args:
        schedule: The active Schedule of Values for the commercial contract.
        line_inputs: Progress entries reported for the current period.
        application_no: Consecutive application number (1, 2, 3...).
        retainage_percent: Contractual retainage percentage (e.g., 10.0 for 10%).
        previous_applications: List of previously accepted billing applications.
        retainage_release_cents: Optional retainage amount released in this application.
        
    Returns:
        ComputedProgressApplication: Deterministic application calculation.
        
    Raises:
        ValueError: If line items exceed 100% of SOV or inputs are inconsistent.
    """
    if retainage_percent < 0.0 or retainage_percent > 100.0:
        raise ValueError(f"Retainage percentage must be between 0.0 and 100.0 (received: {retainage_percent})")
    if retainage_release_cents < 0:
        raise ValueError(f"Retainage release cannot be negative (received: {retainage_release_cents})")

    input_map = {inp.schedule_item_id: inp for inp in line_inputs}

    # Track historical completion per SOV item
    historical_completed_map: dict[str, int] = {item.id: 0 for item in schedule}
    total_previous_billed_gross = 0
    total_previous_retainage_held = 0

    if previous_applications:
        for prev_app in previous_applications:
            total_previous_billed_gross += prev_app.current_payment_due_gross_cents
            total_previous_retainage_held += (
                prev_app.retainage_withheld_this_period_cents - prev_app.retainage_released_this_period_cents
            )
            for prev_item in prev_app.items:
                historical_completed_map[prev_item.schedule_item_id] = prev_item.total_completed_to_date_cents

    computed_items: list[ComputedProgressItem] = []
    total_completed_to_date_all = 0
    total_sov_value = sum(item.scheduled_value_cents for item in schedule)

    for sov_item in schedule:
        prev_completed = historical_completed_map.get(sov_item.id, 0)
        curr_input = input_map.get(
            sov_item.id,
            ProgressItemInput(schedule_item_id=sov_item.id, work_completed_cents=0, stored_materials_cents=0),
        )

        curr_work = curr_input.work_completed_cents
        curr_stored = curr_input.stored_materials_cents
        current_period_total = curr_work + curr_stored
        total_to_date = prev_completed + current_period_total

        if total_to_date > sov_item.scheduled_value_cents:
            overage_cents = total_to_date - sov_item.scheduled_value_cents
            raise ValueError(
                f"Schedule of Values overbill error: Item '{sov_item.item_code} - {sov_item.description}' "
                f"scheduled for ${sov_item.scheduled_value_cents / 100:.2f} cannot bill "
                f"${total_to_date / 100:.2f} (exceeds 100% by ${overage_cents / 100:.2f})."
            )

        pct_complete = round((total_to_date / sov_item.scheduled_value_cents) * 100.0, 2) if sov_item.scheduled_value_cents > 0 else 100.0
        balance_to_finish = sov_item.scheduled_value_cents - total_to_date

        computed_items.append(
            ComputedProgressItem(
                schedule_item_id=sov_item.id,
                item_code=sov_item.item_code,
                description=sov_item.description,
                scheduled_value_cents=sov_item.scheduled_value_cents,
                previous_completed_cents=prev_completed,
                current_work_completed_cents=curr_work,
                current_stored_materials_cents=curr_stored,
                total_completed_to_date_cents=total_to_date,
                percentage_complete=pct_complete,
                balance_to_finish_cents=balance_to_finish,
            )
        )
        total_completed_to_date_all += total_to_date

    # Period gross billing = total work and stored materials added this period
    current_period_gross_cents = sum(
        item.current_work_completed_cents + item.current_stored_materials_cents
        for item in computed_items
    )

    # Retainage calculation based on configured percentage
    retainage_factor = retainage_percent / 100.0
    retainage_withheld_cents = int(round(current_period_gross_cents * retainage_factor))

    # Total retainage held before releases
    cumulative_retainage_before_release = total_previous_retainage_held + retainage_withheld_cents
    if retainage_release_cents > cumulative_retainage_before_release:
        raise ValueError(
            f"Cannot release ${retainage_release_cents / 100:.2f} retainage: "
            f"only ${cumulative_retainage_before_release / 100:.2f} currently held."
        )

    net_retainage_held = cumulative_retainage_before_release - retainage_release_cents
    net_payment_due_cents = (current_period_gross_cents - retainage_withheld_cents) + retainage_release_cents

    overall_pct = (
        round((total_completed_to_date_all / total_sov_value) * 100.0, 2)
        if total_sov_value > 0
        else 0.0
    )

    return ComputedProgressApplication(
        application_no=application_no,
        retainage_percent=retainage_percent,
        items=computed_items,
        total_scheduled_value_cents=total_sov_value,
        total_completed_to_date_cents=total_completed_to_date_all,
        previous_certificates_cents=total_previous_billed_gross,
        current_payment_due_gross_cents=current_period_gross_cents,
        retainage_withheld_this_period_cents=retainage_withheld_cents,
        retainage_released_this_period_cents=retainage_release_cents,
        total_retainage_held_cents=net_retainage_held,
        net_payment_due_cents=net_payment_due_cents,
        overall_percent_complete=overall_pct,
    )
