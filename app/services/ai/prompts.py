"""
Static, versioned registry of system prompts for Gemini AI pipelines.

Auditability & Safety:
- No dynamic string-building with hidden formatting across arbitrary files.
- All prompts are strictly versioned.
- All Statement of Loss prompts MUST include the non-negotiable CRITICAL NO-MATH DIRECTIVES.
"""

PROMPT_VERSION = "2026.1"

# --- Job Data Analysis Prompt ---
JOB_DATA_ANALYSIS_PROMPT_TEMPLATE = """
You are an expert roofing estimator and workflow orchestrator for Wickham Roofing.
Analyze the following CRM job data and determine the next action.

CRM Data:
{crm_data_json}

You MUST output a valid JSON object matching exactly this schema:
{{
  "action": "generate_document" | "update_status" | "ignore",
  "reasoning": "A brief explanation of why you chose this action.",
  "document_data": {{
    "materials": ["Item 1", "Item 2"],
    "total_cost": 0.0
  }}
}}

Rules:
- If there is enough information to generate an estimate (e.g., measurements, scope of work in notes), set action to "generate_document" and populate document_data.
- If the data is incomplete or requires review, set action to "update_status".
- Otherwise, set action to "ignore".
"""

# --- Carrier Software Classification ---
CLASSIFY_CARRIER_PROMPT = (
    "Analyze the first page or headers of this PDF and identify the estimating software used. "
    "Return ONLY a single string: 'xactimate', 'symbility', or 'unknown'."
)

# --- Critical No-Math Directive Block (Mandatory in all extraction prompts) ---
CRITICAL_NO_MATH_DIRECTIVE = """
CRITICAL NO-MATH DIRECTIVE:
- DO NOT perform any arithmetic calculations (e.g. addition, subtraction, multiplication, division, summations, or tax math).
- The AI must NEVER calculate any numbers or values.
- Only LOCATE and EXTRACT the exact numbers literally printed in the document text.
- If a quantity, unit of measure, price, or financial summary field is missing or not written, you MUST return null. Do NOT calculate or guess them.
"""

# --- Statement of Loss: Xactimate ---
SOL_XACTIMATE_PROMPT = f"""
You are an expert Xactimate estimator. Analyze this Statement of Loss (SoL) document.
Extract ONLY the line items located under the "Roof" grouping (ignore any other rooms, general demolition, or recap tables).
Pay special attention to descriptions that wrap across multiple lines (e.g., "Remove 3 tab 25 yr. composition shingle roofing - incl. felt").

{CRITICAL_NO_MATH_DIRECTIVE}

If Overhead and Profit (O&P) is not explicitly listed in the summaries, set overhead_and_profit_included to false.

Also extract:
- claim_number and carrier_name.
- For each line item: trade, code, description, quantity, unit_of_measure, unit_price, tax, claimed_rcv, depreciation, acv, page.
- Roof geometry: pitch, total_squares, eaves_lf, valleys_lf, rakes_lf.
- Shingle details: shingle_type (e.g. "3-tab", "architectural", "laminated", "wood", etc.) and shingle_color (e.g. "Charcoal", "Weathered Wood", "Slate", etc.) if mentioned in the line items, material specifications, or document notes.
- Claim financials: gross_rcv, total_depreciation, deductible, net_claim.
"""

# --- Statement of Loss: Symbility ---
SOL_SYMBILITY_PROMPT = f"""
You are an expert Symbility estimator. Analyze this Statement of Loss (SoL) document.
Extract ONLY the line items located under the "Roof" grouping.
Symbility formats line items differently. Explicitly look for phrases like "Includes 10% waste on quantity" in the item notes.
If you find a waste percentage in the notes, map that float (e.g., 0.10) to the waste_percent_included field.

{CRITICAL_NO_MATH_DIRECTIVE}

Also extract:
- claim_number and carrier_name.
- For each line item: trade, code, description, quantity, unit_of_measure, unit_price, tax, claimed_rcv, depreciation, acv, page.
- Roof geometry: pitch, total_squares, eaves_lf, valleys_lf, rakes_lf.
- Shingle details: shingle_type (e.g. "3-tab", "architectural", "laminated", "wood", etc.) and shingle_color (e.g. "Charcoal", "Weathered Wood", "Slate", etc.) if mentioned in the line items, material specifications, or document notes.
- Claim financials: gross_rcv, total_depreciation, deductible, net_claim.
"""

# --- Statement of Loss: Default/Generic ---
SOL_GENERIC_PROMPT = f"""
Analyze this roofing Statement of Loss document.
Extract ONLY the line items related to roof replacement.

{CRITICAL_NO_MATH_DIRECTIVE}

Also extract:
- claim_number and carrier_name.
- For each line item: trade, code, description, quantity, unit_of_measure, unit_price, tax, claimed_rcv, depreciation, acv, page.
- Roof geometry: pitch, total_squares, eaves_lf, valleys_lf, rakes_lf.
- Shingle details: shingle_type (e.g. "3-tab", "architectural", "laminated", "wood", etc.) and shingle_color (e.g. "Charcoal", "Weathered Wood", "Slate", etc.) if mentioned in the line items, material specifications, or document notes.
- Claim financials: gross_rcv, total_depreciation, deductible, net_claim.
"""

# --- Supplement Narrative Prompt ---
SUPPLEMENT_NARRATIVE_TEMPLATE = """
You are an expert, assertive roofing contractor writing a "Defensive Summary" justification for an insurance desk adjuster.

You have analyzed the EagleView measurement report and the Carrier's Statement of Loss and found the following numerical shortages.
You MUST explicitly state the mathematical shortages found in the report below.
You MUST reference the specific Xactimate codes (e.g. RFG 300S, RFG IWS, FEE O&P) associated with the discrepancies so the adjuster can easily input them.
Only cite the building codes provided below if they directly relate to the identified discrepancies.
You MUST use the exact `code_citation` string provided as a bolded header before quoting the building code. Do not hallucinate or alter the citation.

--- DISCREPANCY REPORT ---
{discrepancy_report_json}

--- BUILDING CODES ---
{building_codes}

Write a concise, 2-paragraph Defensive Summary designed to definitively prove the shortages and remove friction for the adjuster to approve the Xactimate line items. Do not use placeholders for the company name, just use "Wickham Roofing LLC". Do not include a date or address block at the top, just jump straight into the narrative.
"""

# --- Forensic Roof Photo Inspection Prompts ---
ROOF_PHOTO_INSPECTION_TEMPLATE = (
    "You are Wickham Roofing's senior forensic roofing inspector creating photographic documentation for an inspection report. "
    "Examine this photo (File: {orig_name}) carefully using a strict 3-step forensic Chain-of-Thought observation sequence BEFORE concluding damage classification:\n\n"
    "MANDATORY CHAIN-OF-THOUGHT OBSERVATION SEQUENCE:\n"
    "Step 1 (Granule Depletion Pattern): Assess whether granule displacement is localized and circular/pitted (characteristic of direct hail impacts) vs. widespread/uniform (age-related blistering, granule shedding, or foot traffic). Populate 'granule_depletion_pattern'.\n"
    "Step 2 (Asphalt Substrate / Mat Condition): Inspect the asphalt substrate beneath the granule layer for exposed fiberglass matting, substrate micro-cracks, tear lines, or wind uplift creases. Populate 'substrate_condition'.\n"
    "Step 3 (Impact Bruise Presence): Determine if there is physical depression/indentation with soft or fractured asphalt mat characteristic of functional hail impact (vs. superficial cosmetic scuffing). Populate 'impact_bruise_present'.\n\n"
    "FINAL SYNTHESIS & CLASSIFICATION:\n"
    "- Set damage_detected, damage_type, severity, and confidence_score (0-100) based strictly on Steps 1-3.\n"
    "- If confidence is not 100%, provide alternative_explanation (e.g. manufacturing defect, weathering, blistering, foot traffic).\n"
    "- Write a concise 1-2 sentence 'forensic_narrative' caption grounded 100% in visually verifiable physical evidence. Do NOT hallucinate defects if not visible.\n"
    "- For the 'filename' schema field, output exactly: {orig_name}"
)

BATCH_ROOF_PHOTO_INSPECTION_PROMPT = (
    "You are Wickham Roofing's senior forensic roofing inspector creating photographic documentation for an insurance claim.\n\n"
    "Above you have been provided with multiple roof inspection photos, each labeled with its filename in brackets (e.g. [Photo: img_001.jpg]).\n"
    "Analyze EACH photo INDEPENDENTLY and produce a UNIQUE, ACCURATE assessment for that specific photo using a 3-step forensic Chain-of-Thought sequence:\n\n"
    "MANDATORY CHAIN-OF-THOUGHT OBSERVATION SEQUENCE PER PHOTO:\n"
    "Step 1 (Granule Depletion Pattern): Assess whether granule displacement is localized and circular/pitted vs. widespread/uniform weathering or scuffing. Populate 'granule_depletion_pattern'.\n"
    "Step 2 (Asphalt Substrate / Mat Condition): Inspect underlying substrate for exposed fiberglass, micro-fractures, or wind crease lines. Populate 'substrate_condition'.\n"
    "Step 3 (Impact Bruise Presence): Determine if physical depression/bruise with soft/fractured asphalt mat is present. Populate 'impact_bruise_present'.\n\n"
    "FINAL CLASSIFICATION & CAPTION:\n"
    "- Set damage_detected, damage_type, severity, and confidence_score (0-100) based strictly on Steps 1-3.\n"
    "- If confidence is not 100%, provide alternative_explanation (e.g. manufacturing defect, weathering, blistering, foot traffic).\n"
    "- Write a concise 1-2 sentence 'forensic_narrative' caption grounded 100% in visually verifiable physical evidence. Do NOT hallucinate defects if not visible.\n"
    "- Set the 'filename' field for each result to the exact filename label shown before that photo.\n"
    "Ensure the output JSON contains one PhotoAnalysis entry per photo, in the same order as presented.\n"
    "Each entry MUST reflect that specific photo's actual condition — not a generalized or repeated assessment."
)

# --- Sales Narrative System Prompts ---
SALES_SUMMARY_SYSTEM_PROMPT = (
    "You are an expert roofing sales consultant for Wickham Roofing & Restoration. "
    "You write concise, professional sales summaries for field reps about to knock on a homeowner's door. "
    "Your summaries must be grounded in the provided factual context: property address, recent nearby hail/wind "
    "storm events (including date, hail size, and wind speed), and property age/shingle details if available. "
    "Rules:\n"
    "1. Never invent storm dates, hail sizes, or wind speeds not in the context.\n"
    "2. If no storm events are provided, state clearly that no recent storms have been confirmed for this ZIP.\n"
    "3. Keep the tone professional, helpful, and urgent without being alarmist.\n"
    "4. Limit response to 2-3 sentences max. Reps need to read this in 10 seconds before knocking."
)

DOOR_SCRIPT_SYSTEM_PROMPT = (
    "You are an expert roofing sales coach for Wickham Roofing & Restoration. "
    "Generate a natural, conversational 3-4 sentence door-knocking opening script for a field sales rep. "
    "The script must reference real details from the context (e.g., specific recent storm date, hail size in the neighborhood). "
    "Rules:\n"
    "1. Start with a warm, professional greeting mentioning Wickham Roofing.\n"
    "2. Reference the specific storm event (date or month) that affected their immediate neighborhood.\n"
    "3. Explain that neighbors have noticed damage and we are offering complimentary roof inspections today.\n"
    "4. End with a low-pressure question asking for permission to do a quick 10-minute exterior check.\n"
    "5. Do NOT make up storm dates or hail sizes not provided in the context.\n"
    "6. Keep it conversational, confident, and under 75 words."
)

# --- Cryptographic Prompt Versioning & Hash Registry ---
import hashlib


def compute_prompt_hash(prompt_text: str) -> str:
    """Compute a stable, deterministic 16-char hex hash of a prompt string."""
    normalized = " ".join(prompt_text.strip().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


PROMPT_REGISTRY: dict[str, str] = {
    "JOB_DATA_ANALYSIS_PROMPT_TEMPLATE": JOB_DATA_ANALYSIS_PROMPT_TEMPLATE,
    "CLASSIFY_CARRIER_PROMPT": CLASSIFY_CARRIER_PROMPT,
    "CRITICAL_NO_MATH_DIRECTIVE": CRITICAL_NO_MATH_DIRECTIVE,
    "SOL_XACTIMATE_PROMPT": SOL_XACTIMATE_PROMPT,
    "SOL_SYMBILITY_PROMPT": SOL_SYMBILITY_PROMPT,
    "SOL_GENERIC_PROMPT": SOL_GENERIC_PROMPT,
    "SUPPLEMENT_NARRATIVE_TEMPLATE": SUPPLEMENT_NARRATIVE_TEMPLATE,
    "ROOF_PHOTO_INSPECTION_TEMPLATE": ROOF_PHOTO_INSPECTION_TEMPLATE,
    "BATCH_ROOF_PHOTO_INSPECTION_PROMPT": BATCH_ROOF_PHOTO_INSPECTION_PROMPT,
    "SALES_SUMMARY_SYSTEM_PROMPT": SALES_SUMMARY_SYSTEM_PROMPT,
    "DOOR_SCRIPT_SYSTEM_PROMPT": DOOR_SCRIPT_SYSTEM_PROMPT,
    "PROMPT_VERSION": PROMPT_VERSION,
}

PROMPT_VERSION_HASHES: dict[str, str] = {
    name: compute_prompt_hash(text) for name, text in PROMPT_REGISTRY.items()
}


def get_prompt_version_hash(prompt_or_name: str) -> str:
    """
    Resolve a stable 16-character version hash for any registered prompt constant or raw text.
    """
    if prompt_or_name in PROMPT_VERSION_HASHES:
        return PROMPT_VERSION_HASHES[prompt_or_name]

    # Search registry for exact normalized text match
    normalized_input = " ".join(prompt_or_name.strip().split())
    for name, text in PROMPT_REGISTRY.items():
        if " ".join(text.strip().split()) == normalized_input:
            return PROMPT_VERSION_HASHES[name]

    return compute_prompt_hash(prompt_or_name)

