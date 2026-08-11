import sqlite3

import structlog
from passlib.context import CryptContext

logger = structlog.get_logger("app.migrations.0003")
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def up(conn: sqlite3.Connection) -> None:
    """
    Hash all existing plaintext PINs in the field_reps table.
    """
    logger.info("running_migration_0003_hash_field_rep_pins")
    
    # We rename the column to pin_hash for clarity and to prevent old plaintext code from working
    conn.execute("ALTER TABLE field_reps RENAME COLUMN pin TO pin_hash")
    conn.execute("DROP INDEX IF EXISTS idx_field_reps_pin")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_field_reps_pin_hash ON field_reps(pin_hash)")
    
    cursor = conn.execute("SELECT id, pin_hash FROM field_reps")
    rows = cursor.fetchall()
    
    for row in rows:
        rep_id = row["id"]
        pin_val = row["pin_hash"]
        
        # If it's not already a bcrypt hash
        if pin_val and not pin_val.startswith("$2b$") and not pin_val.startswith("$2a$"):
            hashed_pin = pwd_context.hash(pin_val)
            conn.execute("UPDATE field_reps SET pin_hash = ? WHERE id = ?", (hashed_pin, rep_id))
            
    logger.info("migration_0003_complete")
