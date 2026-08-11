import os
import sys
import shutil
import uuid

# Add the project root to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.core.database import get_connection, create_field_rep

def clear_directory_contents(dir_path):
    if not os.path.exists(dir_path):
        return
    for filename in os.listdir(dir_path):
        file_path = os.path.join(dir_path, filename)
        try:
            if os.path.isfile(file_path) or os.path.islink(file_path):
                # Don't delete .gitkeep if it exists
                if filename != '.gitkeep':
                    os.unlink(file_path)
            elif os.path.isdir(file_path):
                shutil.rmtree(file_path)
        except Exception as e:
            print(f"Failed to delete {file_path}. Reason: {e}")

def reset_demo_db():
    conn = get_connection()

    # Tables are deleted in reverse dependency order.
    # Jobs and field_reps are deleted last.
    tables_to_clear = [
        "ai_usage_logs",
        "storm_verifications",
        "supplement_flags",
        "supplement_reports",
        "job_tasks",
        "job_documents",
        "supplements",
        "job_agreements",
        "financials",
        "schedule",
        "material_orders",
        "jobs",
        "field_reps"
    ]

    try:
        # Clear transactional tables
        for table in tables_to_clear:
            conn.execute(f"DELETE FROM {table}")

        conn.commit()
        print(f"Successfully cleared tables: {', '.join(tables_to_clear)}")

    except Exception as e:
        print(f"Error resetting demo database: {e}")
        conn.rollback()
    finally:
        conn.close()

    # Re-seed core team reps (Michael, Scott, Debi) and demo rep Jerry Grubb
    from app.core.database import seed_core_team_reps
    seed_core_team_reps()
    try:
        create_field_rep("Jerry Grubb", "1111")
        print("Successfully created demo field rep 'Jerry Grubb' with PIN 1111.")
    except Exception as e:
        print(f"Demo field rep 'Jerry Grubb' creation note: {e}")

    try:
        from app.core.cache import init_db as init_cache_db, _get_connection as get_cache_connection
        init_cache_db()
        with get_cache_connection() as cache_conn:
            cache_conn.execute("DELETE FROM analysis_cache")
            cache_conn.commit()
        print("Successfully cleared AI photo analysis cache.")
    except Exception as e:
        print(f"AI photo analysis cache reset note: {e}")

    # No demo job seeded for a clean slate.
    pass

    # Wipe contents of document/upload directories
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    dirs_to_clear = [
        os.path.join(base_dir, 'data', 'field_docs'),
        os.path.join(base_dir, 'field_docs'),
        os.path.join(base_dir, 'field_photos'),
        os.path.join(base_dir, 'generated_exports'),
        os.path.join(base_dir, 'signed_agreements')
    ]

    for d in dirs_to_clear:
        clear_directory_contents(d)
        print(f"Cleared contents of {os.path.relpath(d, base_dir)}")

if __name__ == "__main__":
    reset_demo_db()
