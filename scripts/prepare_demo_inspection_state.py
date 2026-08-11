import os
import sys
import sqlite3
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.core.database import get_connection

def prepare_demo_state():
    conn = get_connection()
    try:
        # Fetch the main demo job ID
        cursor = conn.execute("SELECT id, homeowner_name FROM jobs LIMIT 1")
        row = cursor.fetchone()
        if not row:
            print("No jobs found in DB!")
            return
        
        job_id = row["id"]
        homeowner = row["homeowner_name"]
        print(f"Targeting demo job {job_id} ({homeowner})")

        # 1. Update job status to LEAD_CAPTURED (unsigned intake state)
        conn.execute("UPDATE jobs SET status = 'LEAD_CAPTURED' WHERE id = ?", (job_id,))

        # 2. Remove generated report & grid docs from job_documents
        conn.execute(
            "DELETE FROM job_documents WHERE job_id = ? AND category IN ('INSPECTION_REPORT', 'HOMEOWNER_INSPECTION_REPORT', 'EVIDENCE_GRID')",
            (job_id,)
        )
        conn.commit()
        print("Job status updated to LEAD_CAPTURED and generated report/grid docs removed.")

        # Print remaining docs
        docs = conn.execute("SELECT filename, category FROM job_documents WHERE job_id = ?", (job_id,)).fetchall()
        print(f"Remaining documents in DB ({len(docs)}):")
        for d in docs:
            print(f"  - [{d['category']}] {d['filename']}")

    finally:
        conn.close()

    # 3. Clean generated inspection PDFs from disk
    doc_dir = Path("data") / "field_docs" / job_id
    if doc_dir.exists():
        for fname in ["inspection_report_homeowner.pdf", "evidence_grid.pdf"]:
            pdf_path = doc_dir / fname
            if pdf_path.exists():
                pdf_path.unlink()
                print(f"Deleted PDF from disk: {pdf_path}")

    # 4. Clear analysis cache DB
    cache_db = Path("data") / "cache.db"
    if cache_db.exists():
        try:
            c_conn = sqlite3.connect(cache_db)
            c_conn.execute("DELETE FROM analysis_cache")
            c_conn.commit()
            c_conn.close()
            print("Successfully cleared analysis_cache DB table.")
        except Exception as err:
            print(f"Cache clear note: {err}")

if __name__ == "__main__":
    prepare_demo_state()
