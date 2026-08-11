import sqlite3

import structlog

logger = structlog.get_logger("app.core.migrations.0001_initial_schema")

def up(conn: sqlite3.Connection) -> None:
    """Apply the initial schema."""
    logger.info("applying_migration", version=1, name="initial_schema")

    """Initialize the jobs table and associated schemas if they do not exist.
    
    Creates the jobs, material_orders, schedule, financials, and pricing tables.
    Also seeds the database with default pricing values.
    
    Raises:
        Exception: If database initialization fails.
    """
    
    try:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                invoice_id TEXT UNIQUE,
                homeowner_name TEXT NOT NULL,
                address_line1 TEXT NOT NULL,
                city TEXT NOT NULL,
                state TEXT NOT NULL,
                postal_code TEXT NOT NULL,
                phone TEXT NOT NULL,
                email TEXT,
                claim_number TEXT,
                insurer_name TEXT,
                status TEXT DEFAULT 'LEAD_CAPTURED',
                status_history TEXT,
                inspector_name TEXT,
                inspection_date TIMESTAMP,
                inspection_notes TEXT,
                ice_barrier_required BOOLEAN,
                jurisdiction_code_version TEXT DEFAULT '2021_IRC',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Lightweight migration if jobs existed before inspection fields
        for col in ["inspector_name TEXT", "inspection_date TIMESTAMP", "inspection_notes TEXT",
                    "job_type TEXT DEFAULT 'INSURANCE'", "policy_type TEXT", "adjuster_name TEXT", "adjuster_phone TEXT",
                    "adjuster_email TEXT", "canvasser_name TEXT", "qbo_customer_id TEXT",
                    "ice_barrier_required BOOLEAN", "jurisdiction_code_version TEXT DEFAULT '2021_IRC'", "invoiced_at TIMESTAMP"]:
            try:
                conn.execute(f"ALTER TABLE jobs ADD COLUMN {col}")
            except sqlite3.OperationalError:
                pass # Column already exists
                
        # Operations material confirmation flags on the jobs table
        for col in [
            "materials_ordered INTEGER NOT NULL DEFAULT 0",
            "materials_on_site INTEGER NOT NULL DEFAULT 0",
        ]:
            try:
                conn.execute(f"ALTER TABLE jobs ADD COLUMN {col}")
            except sqlite3.OperationalError:
                pass  # Column already exists
                

                
        # Phase 5 & 6: Operations columns on the jobs table
        for col in [
            "supplement_sent_at TIMESTAMP",
            "acv_received INTEGER DEFAULT 0",
            "supplement_received INTEGER DEFAULT 0",
            "acv_received_at TIMESTAMP",
            "supplement_received_at TIMESTAMP",
            "pipeline_error_message TEXT",
            "ev_total_area_sf REAL",
            "ev_predominant_pitch TEXT",
            "ev_ridge_lf REAL",
            "ev_hip_lf REAL",
            "ev_valley_lf REAL",
            "ev_eaves_lf REAL",
            "ev_rakes_lf REAL",
            "invoice_id TEXT",
            "commission_ready INTEGER DEFAULT 0",
            "commission_pdf_path TEXT",
            "commission_generated_at TIMESTAMP",
            "escalation_sent_at TIMESTAMP",
            "carrier_sla_days INTEGER DEFAULT 14",
            "canvasser_rep_id TEXT"
        ]:
            try:
                conn.execute(f"ALTER TABLE jobs ADD COLUMN {col}")
            except sqlite3.OperationalError:
                pass
        
        conn.execute('''
            CREATE TABLE IF NOT EXISTS material_orders (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                supplier_name TEXT NOT NULL,
                delivery_date TIMESTAMP,
                bom_json TEXT NOT NULL,
                FOREIGN KEY(job_id) REFERENCES jobs(id)
            )
        ''')
        
        conn.execute('''
            CREATE TABLE IF NOT EXISTS schedule (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                crew_name TEXT NOT NULL,
                install_date TIMESTAMP,
                delivery_date TIMESTAMP,
                status TEXT NOT NULL,
                FOREIGN KEY(job_id) REFERENCES jobs(id)
            )
        ''')
        
        conn.execute('''
            CREATE TABLE IF NOT EXISTS financials (
                job_id TEXT PRIMARY KEY,
                revenue_cents INTEGER NOT NULL DEFAULT 0,
                carrier_rcv_cents INTEGER NOT NULL DEFAULT 0,
                material_cost_cents INTEGER NOT NULL DEFAULT 0,
                labor_cost_cents INTEGER NOT NULL DEFAULT 0,
                permits_fee_cents INTEGER NOT NULL DEFAULT 0,
                overhead_pct REAL NOT NULL DEFAULT 0.0,
                canvasser_commission_pct REAL NOT NULL DEFAULT 0.0,
                deductible_cents INTEGER DEFAULT 0,
                acv_payment_cents INTEGER DEFAULT 0,
                recoverable_depreciation_cents INTEGER DEFAULT 0,
                carrier_initial_rcv_cents INTEGER DEFAULT NULL,
                carrier_supplemented_rcv_cents INTEGER DEFAULT NULL,
                qbo_invoice_id TEXT,
                qbo_exported INTEGER NOT NULL DEFAULT 0,
                qbo_exported_at TIMESTAMP,
                FOREIGN KEY(job_id) REFERENCES jobs(id)
            )
        ''')
        


        conn.execute('''
            CREATE TABLE IF NOT EXISTS qbo_credentials (
                realm_id TEXT PRIMARY KEY,
                access_token TEXT NOT NULL,
                refresh_token TEXT NOT NULL,
                token_expires_at TIMESTAMP NOT NULL,
                refresh_expires_at TIMESTAMP NOT NULL
            )
        ''')

        conn.execute('''
            CREATE TABLE IF NOT EXISTS job_agreements (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                type TEXT NOT NULL,
                pdf_path TEXT,
                signature_image_path TEXT,
                signed_at TIMESTAMP NOT NULL,
                signed_by_name TEXT,
                signed_by_ip TEXT,
                user_agent TEXT,
                FOREIGN KEY(job_id) REFERENCES jobs(id)
            )
        ''')
            
        conn.execute('''
            CREATE TABLE IF NOT EXISTS supplements (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                round_number INTEGER NOT NULL,
                pdf_path TEXT,
                submitted_at TIMESTAMP,
                status TEXT,
                FOREIGN KEY(job_id) REFERENCES jobs(id)
            )
        ''')
            
        conn.execute('''
            CREATE TABLE IF NOT EXISTS qbo_mappings (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                qbo_customer_id TEXT,
                qbo_invoice_id TEXT,
                FOREIGN KEY(job_id) REFERENCES jobs(id)
            )
        ''')
            
        conn.execute('''
            CREATE TABLE IF NOT EXISTS job_documents (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                file_type TEXT NOT NULL,
                storage_path TEXT NOT NULL,
                sha256_hash TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(job_id) REFERENCES jobs(id)
            )
        ''')
        
        # Lightweight migration for job_documents sha256_hash
        try:
            conn.execute("ALTER TABLE job_documents ADD COLUMN sha256_hash TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_job_documents_hash ON job_documents(job_id, sha256_hash)")
        except sqlite3.OperationalError:
            pass # Column already exists
            
        conn.execute('''
            CREATE TABLE IF NOT EXISTS supplement_reports (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL REFERENCES jobs(id),
                report_json TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_supplement_reports_job "
            "ON supplement_reports(job_id, created_at DESC)"
        )
            
        conn.execute('''
            CREATE TABLE IF NOT EXISTS ai_usage_logs (
                id TEXT PRIMARY KEY,
                job_id TEXT,
                tokens_used INTEGER NOT NULL,
                model_name TEXT NOT NULL,
                operation_type TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS pricing (
                item_key TEXT PRIMARY KEY,
                default_rate REAL NOT NULL
            )
        ''')
        
        conn.execute('''
            CREATE TABLE IF NOT EXISTS storm_verifications (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                loss_date TIMESTAMP NOT NULL,
                event_type TEXT NOT NULL,
                magnitude REAL,
                begin_lat REAL NOT NULL,
                begin_lon REAL NOT NULL,
                distance_miles REAL,
                match_confidence TEXT NOT NULL,
                FOREIGN KEY(job_id) REFERENCES jobs(id)
            )
        ''')
        
        conn.execute('''
            CREATE TABLE IF NOT EXISTS supplement_rules (
                id TEXT PRIMARY KEY,
                parent_code TEXT NOT NULL,
                required_child_code TEXT NOT NULL,
                citation_text TEXT NOT NULL,
                citation_type TEXT NOT NULL CHECK(citation_type IN ('IRC', 'MFG_SPEC', 'INTERNAL_POLICY')),
                trigger_logic_name TEXT NOT NULL,
                climate_dependent BOOLEAN DEFAULT 0
            )
        ''')
        
        try:
            conn.execute("ALTER TABLE supplement_rules ADD COLUMN climate_dependent BOOLEAN DEFAULT 0")
        except sqlite3.OperationalError:
            pass # Column already exists
        
        conn.execute('''
            CREATE TABLE IF NOT EXISTS supplement_flags (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                rule_id TEXT NOT NULL,
                triggered INTEGER NOT NULL DEFAULT 0,
                quantity_delta REAL NOT NULL DEFAULT 0.0,
                notes TEXT,
                FOREIGN KEY(job_id) REFERENCES jobs(id),
                FOREIGN KEY(rule_id) REFERENCES supplement_rules(id)
            )
        ''')
        
        # STATE MACHINE V2 MIGRATION NOTE:
        # JobStatus.PENDING_OPERATOR_REVIEW and MATERIALS_ON_SITE are new 
        # TEXT values. Existing rows are unaffected. No ALTER TABLE required.
        # The is_operator_gate() classmethod enforces the two-track boundary
        # at the application layer, not the DB layer.

        # V3 MIGRATION: PENDING_MANUAL_REVIEW retired Phase 7.
        # Transition orphaned rows to PENDING_OPERATOR_REVIEW.
        conn.execute("""
            UPDATE jobs
            SET status = 'PENDING_OPERATOR_REVIEW',
                pipeline_error_message =
                    'Migrated from PENDING_MANUAL_REVIEW (Phase 7)'
            WHERE status = 'PENDING_MANUAL_REVIEW'
        """)

        # V4 MIGRATION: APPRAISAL_INVOKED is a new terminal state (Phase 8).
        # No orphaned rows expected. Included for audit trail completeness.
        conn.execute("""
            UPDATE jobs
            SET status = 'APPRAISAL_INVOKED',
                pipeline_error_message =
                    'Escalation SLA exceeded twice (Phase 8)'
            WHERE status = 'APPRAISAL_INVOKED'
              AND 1=0
        """)
        
        conn.execute('''
            CREATE TABLE IF NOT EXISTS job_tasks (
                job_id TEXT,
                task_type TEXT,
                phase TEXT CHECK(phase IN ('queued','running','completed','failed')),
                last_error TEXT,
                PRIMARY KEY(job_id, task_type)
            )
        ''')

        # V5 MIGRATION: field_reps table introduced Phase 9.
        # No data migration required. Existing static field_pin
        # in config.py remains as a config-level default only.
        conn.execute('''
            CREATE TABLE IF NOT EXISTS field_reps (
                id          TEXT PRIMARY KEY,
                name        TEXT NOT NULL,
                pin         TEXT NOT NULL UNIQUE,
                is_active   INTEGER NOT NULL DEFAULT 1,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.execute("CREATE INDEX IF NOT EXISTS idx_job_docs_job ON job_documents(job_id)")
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_job_docs_hash ON job_documents(job_id, sha256_hash)")
        except sqlite3.OperationalError:
            pass
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_supp_flags_unique ON supplement_flags(job_id, rule_id)")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "idx_field_reps_pin ON field_reps(pin)"
        )

        conn.execute('''
            CREATE VIEW IF NOT EXISTS live_material_board AS
            SELECT 
                j.id as job_id,
                j.homeowner_name,
                j.status,
                m.supplier_name,
                m.delivery_date,
                CAST(json_extract(m.bom_json, '$.total_squares') * 1.15 + 0.99 AS INTEGER) AS shingle_squares_required,
                CAST((json_extract(m.bom_json, '$.eaves_lf') + json_extract(m.bom_json, '$.valleys_lf')) / 66.0 + 0.99 AS INTEGER) AS ice_and_water_rolls,
                CAST((json_extract(m.bom_json, '$.eaves_lf') + json_extract(m.bom_json, '$.rakes_lf')) / 10.0 + 0.99 AS INTEGER) AS drip_edge_pieces_10ft,
                CAST((json_extract(m.bom_json, '$.eaves_lf') + json_extract(m.bom_json, '$.rakes_lf')) / 100.0 + 0.99 AS INTEGER) AS starter_bundles
            FROM jobs j
            JOIN material_orders m ON j.id = m.job_id
        ''')
        

        conn.execute('''
            CREATE VIEW IF NOT EXISTS financial_delta_view AS
            SELECT 
                j.id as job_id,
                j.homeowner_name,
                f.carrier_initial_rcv_cents,
                f.carrier_supplemented_rcv_cents,
                f.revenue_cents,
                (f.carrier_supplemented_rcv_cents - f.carrier_initial_rcv_cents) AS carrier_rcv_delta_cents,
                (f.revenue_cents - f.carrier_supplemented_rcv_cents) AS contractor_over_carrier_cents
            FROM jobs j
            JOIN financials f ON j.id = f.job_id
        ''')

        conn.execute("""
            CREATE TABLE IF NOT EXISTS invoice_sequence (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                last_seq INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute("""
            INSERT OR IGNORE INTO invoice_sequence (id, last_seq)
            VALUES (1, 0)
        """)


    except Exception as e:
        logger.error("database_initialization_failed", error=str(e))
        raise
    finally:
        pass

