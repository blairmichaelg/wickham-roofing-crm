import re
from pathlib import Path

import structlog

from app.core.supplement_models import CodeSection, DiscrepancyReport

logger = structlog.get_logger("app.core.code_router")

# Map discrepancy categories to specific XML tags required for justification
DISCREPANCY_TO_CODE_MAP = {
    "Missing Drip Edge": ["SECTION_R905_2_8_5", "GA_R905_2_8_5"],
    "Missing Ice & Water Shield": ["SECTION_R905_2_8_2"],
    "Area Shortage": ["SECTION_R905_2_2"],
    "Ridge/Hip Cap Shortage": ["SECTION_R905_2_2"],
    "Missing O&P": []
}

def parse_code_files(directory_path: str = "building_codes") -> dict[str, CodeSection]:
    """
    Reads all .txt files in the directory and extracts XML tags and their contents.
    Returns a dictionary mapping tag names to their CodeSection models.
    """
    code_index: dict[str, CodeSection] = {}
    dir_path = Path(directory_path)
    
    if not dir_path.exists():
        logger.warning("code_router_dir_not_found", path=directory_path)
        return code_index
        
    # Match <TAG_NAME>content</TAG_NAME> across multiple lines
    tag_pattern = re.compile(r"<([A-Z0-9_]+)>(.*?)</\1>", re.DOTALL)
    
    for txt_file in dir_path.glob("*.txt"):
        try:
            content = txt_file.read_text(encoding="utf-8")
            
            # Infer metadata from root tags
            jurisdiction = "National"
            edition = "2024"  # Default
            code_set = "IRC"
            
            if "<GEORGIA_AMENDMENTS>" in content:
                jurisdiction = "GA"
            
            # Remove root wrapper tags so findall can extract the actual code sections
            content = re.sub(r"</?(GEORGIA_AMENDMENTS|IRC_CHAPTER_9)>", "", content)
            
            matches = tag_pattern.findall(content)
            for tag, text in matches:
                # Extract section number from tag (e.g., SECTION_R905_2_8_5 -> R905.2.8.5)
                # GA_R905_2_8_5 -> R905.2.8.5
                section_str = tag.replace("SECTION_", "").replace("GA_", "").replace("_", ".")
                
                code_index[tag.strip()] = CodeSection(
                    code_set=code_set,
                    edition=edition,
                    jurisdiction=jurisdiction,
                    section=section_str,
                    text=text.strip()
                )
        except Exception as e:
            logger.error("code_router_parse_error", file=txt_file.name, error=str(e))
            
    return code_index

def get_relevant_codes(report: DiscrepancyReport, code_index: dict[str, CodeSection]) -> str:
    """
    Looks up required XML tags based on the report's discrepancies,
    retrieves the text from the index, and constructs exact citation strings.
    Mutates the report to attach the code_citation to each discrepancy.
    """
    relevant_texts = {}
    
    for disc in report.discrepancies:
        if disc.category in DISCREPANCY_TO_CODE_MAP:
            # For this discrepancy, collect the citations
            disc_citations = []
            
            for tag in DISCREPANCY_TO_CODE_MAP[disc.category]:
                if tag in code_index:
                    code_sec = code_index[tag]
                    
                    # Construct exact citation string
                    if code_sec.jurisdiction != "National":
                        citation = f"{code_sec.code_set} {code_sec.edition} ({code_sec.jurisdiction} Amendments) Section {code_sec.section}"
                    else:
                        citation = f"{code_sec.code_set} {code_sec.edition} Section {code_sec.section}"
                    
                    disc_citations.append(citation)
                    
                    # Store for the narrative context string
                    if citation not in relevant_texts:
                        relevant_texts[citation] = f"**{citation}**\n{code_sec.text}"
            
            # Attach the exact citation string to the discrepancy model
            if disc_citations:
                disc.code_citation = " and ".join(disc_citations)

    # Return the aggregated citations for the AI prompt
    # Sort for deterministic output
    sorted_texts = [relevant_texts[cite] for cite in sorted(relevant_texts.keys())]
    return "\n\n".join(sorted_texts)
