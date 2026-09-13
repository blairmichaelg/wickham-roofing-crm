"""
app/services/evidence_matrix.py — Business logic and data management for Evidence Matrix v1.
Manages structured photo/field observation exhibits linked to jobs and claim line items.
Schema matches Migration 0028: evidence_exhibits table.
"""

from __future__ import annotations

import datetime
import sqlite3
import uuid
from typing import Any

from app.core.database import get_db_connection

CATEGORY_MAP = {
    "decking": "decking_sheathing",
    "decking_sheathing": "decking_sheathing",
    "flashing": "flashing_penetration",
    "flashing_penetration": "flashing_penetration",
    "membrane_shingle_damage": "shingle_damage",
    "shingle_damage": "shingle_damage",
    "shingle": "shingle_damage",
    "ventilation": "ventilation",
    "ice_and_water": "code_upgrade",
    "code_upgrade": "code_upgrade",
    "code": "code_upgrade",
    "interior": "interior_water_damage",
    "interior_water_damage": "interior_water_damage",
    "debris_access": "debris_access",
    "other": "other",
}

EVIDENCE_CATEGORIES = {
    "decking_sheathing": "Decking / Sheathing",
    "flashing_penetration": "Flashing / Penetration",
    "shingle_damage": "Membrane / Shingle Damage",
    "ventilation": "Ventilation",
    "code_upgrade": "Ice & Water / Code-Related Upgrade",
    "interior_water_damage": "Interior Water Damage",
    "debris_access": "Debris / Access / Staging",
    "other": "Other Roof Component",
}

VALID_STATUSES = {"draft", "reviewed", "included", "excluded"}


def create_evidence_exhibit(
    job_id: str,
    category: str,
    observation: str,
    location_roof_area: str | None = None,
    document_id: str | None = None,
    photo_path: str | None = None,
    proposed_claim_line_ref: str | None = None,
    author_rep_id: str | None = None,
    source_type: str = "FIELD_REP_OBSERVATION",
    status: str = "draft",
    db_path: str = "data/wickham_crm.db",
) -> dict[str, Any]:
    """
    Create a new evidence exhibit for a job.
    Auto-assigns the next exhibit_number in sequence for the job.
    """
    schema_category = CATEGORY_MAP.get(category, "other")
    if status not in VALID_STATUSES:
        status = "draft"

    exhibit_id = f"ex_{uuid.uuid4().hex[:12]}"
    now = datetime.datetime.now(datetime.UTC).isoformat()

    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COALESCE(MAX(exhibit_number), 0) FROM evidence_exhibits WHERE job_id = ?",
            (job_id,),
        )
        row = cursor.fetchone()
        next_num = (row[0] if row else 0) + 1

        cursor.execute(
            """
            INSERT INTO evidence_exhibits (
                id, job_id, photo_id, photo_path, category,
                observation_text, roof_area_location, exhibit_number,
                status, ast_discrepancy_key, requires_office_review,
                source_provenance, created_by, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
            """,
            (
                exhibit_id,
                job_id,
                document_id,
                photo_path,
                schema_category,
                observation.strip(),
                location_roof_area.strip() if location_roof_area else None,
                next_num,
                status,
                proposed_claim_line_ref,
                source_type,
                author_rep_id,
                now,
                now,
            ),
        )
        conn.commit()

    return {
        "id": exhibit_id,
        "job_id": job_id,
        "exhibit_number": next_num,
        "category": schema_category,
        "category_display": EVIDENCE_CATEGORIES.get(schema_category, "Other"),
        "observation": observation.strip(),
        "observation_text": observation.strip(),
        "location_roof_area": location_roof_area,
        "roof_area_location": location_roof_area,
        "document_id": document_id,
        "photo_id": document_id,
        "photo_path": photo_path,
        "proposed_claim_line_ref": proposed_claim_line_ref,
        "ast_discrepancy_key": proposed_claim_line_ref,
        "source_type": source_type,
        "source_provenance": source_type,
        "status": status,
        "requires_office_review": True,
        "author_rep_id": author_rep_id,
        "created_by": author_rep_id,
        "created_at": now,
        "updated_at": now,
    }


def get_job_evidence_exhibits(
    job_id: str,
    status: str | None = None,
    included_only: bool = False,
    db_path: str = "data/wickham_crm.db",
) -> list[dict[str, Any]]:
    """
    Retrieve all evidence exhibits for a job ordered by exhibit_number.
    """
    with get_db_connection(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        if included_only:
            cursor.execute(
                """
                SELECT * FROM evidence_exhibits
                WHERE job_id = ? AND status = 'included'
                ORDER BY exhibit_number ASC, created_at ASC
                """,
                (job_id,),
            )
        elif status:
            cursor.execute(
                """
                SELECT * FROM evidence_exhibits
                WHERE job_id = ? AND status = ?
                ORDER BY exhibit_number ASC, created_at ASC
                """,
                (job_id, status),
            )
        else:
            cursor.execute(
                """
                SELECT * FROM evidence_exhibits
                WHERE job_id = ?
                ORDER BY exhibit_number ASC, created_at ASC
                """,
                (job_id,),
            )
        rows = cursor.fetchall()

    exhibits = []
    for r in rows:
        d = dict(r)
        cat = d.get("category", "other")
        d["category_display"] = EVIDENCE_CATEGORIES.get(cat, "Other")
        d["requires_office_review"] = bool(d.get("requires_office_review", 0))
        # Provide both naming conventions for callers
        d["observation"] = d.get("observation_text", "")
        d["location_roof_area"] = d.get("roof_area_location")
        d["proposed_claim_line_ref"] = d.get("ast_discrepancy_key")
        d["source_type"] = d.get("source_provenance", "FIELD_REP_OBSERVATION")
        d["author_rep_id"] = d.get("created_by")
        exhibits.append(d)
    return exhibits


def update_evidence_exhibit(
    exhibit_id: str,
    category: str | None = None,
    observation: str | None = None,
    location_roof_area: str | None = None,
    proposed_claim_line_ref: str | None = None,
    status: str | None = None,
    requires_office_review: bool | None = None,
    db_path: str = "data/wickham_crm.db",
) -> dict[str, Any] | None:
    """
    Update an exhibit record (e.g. office review approval or field note correction).
    """
    now = datetime.datetime.now(datetime.UTC).isoformat()
    fields: list[str] = []
    params: list[Any] = []

    if category is not None:
        schema_cat = CATEGORY_MAP.get(category, "other")
        fields.append("category = ?")
        params.append(schema_cat)
    if observation is not None:
        fields.append("observation_text = ?")
        params.append(observation.strip())
    if location_roof_area is not None:
        fields.append("roof_area_location = ?")
        params.append(location_roof_area.strip() or None)
    if proposed_claim_line_ref is not None:
        fields.append("ast_discrepancy_key = ?")
        params.append(proposed_claim_line_ref.strip() or None)
    if status is not None and status in VALID_STATUSES:
        fields.append("status = ?")
        params.append(status)
    if requires_office_review is not None:
        fields.append("requires_office_review = ?")
        params.append(1 if requires_office_review else 0)

    if not fields:
        return None

    fields.append("updated_at = ?")
    params.append(now)
    params.append(exhibit_id)

    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"UPDATE evidence_exhibits SET {', '.join(fields)} WHERE id = ?",
            params,
        )
        conn.commit()

        conn.row_factory = sqlite3.Row
        c2 = conn.cursor()
        c2.execute("SELECT * FROM evidence_exhibits WHERE id = ?", (exhibit_id,))
        row = c2.fetchone()
        if not row:
            return None
        res = dict(row)
        cat = res.get("category", "other")
        res["category_display"] = EVIDENCE_CATEGORIES.get(cat, "Other")
        res["requires_office_review"] = bool(res.get("requires_office_review", 0))
        res["observation"] = res.get("observation_text", "")
        res["location_roof_area"] = res.get("roof_area_location")
        res["proposed_claim_line_ref"] = res.get("ast_discrepancy_key")
        res["source_type"] = res.get("source_provenance", "FIELD_REP_OBSERVATION")
        res["author_rep_id"] = res.get("created_by")
        return res


def reorder_evidence_exhibits(
    job_id: str,
    exhibit_ids_in_order: list[str],
    db_path: str = "data/wickham_crm.db",
) -> bool:
    """
    Update exhibit_number based on given ID order.
    """
    now = datetime.datetime.now(datetime.UTC).isoformat()
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        for idx, ex_id in enumerate(exhibit_ids_in_order, start=1):
            cursor.execute(
                """
                UPDATE evidence_exhibits
                SET exhibit_number = ?, updated_at = ?
                WHERE id = ? AND job_id = ?
                """,
                (idx, now, ex_id, job_id),
            )
        conn.commit()
    return True
