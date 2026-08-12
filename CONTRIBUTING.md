# CONTRIBUTING TO WICKHAM ROOFING CRM

## Getting Started for Collaborators

This section gets you from a fresh clone to a running, verified local development environment in under 10 minutes.

### 1. Clone & Set Up

```powershell
git clone https://github.com/blairmichaelg/wickham-roofing-crm.git
cd wickham-roofing-crm

python -m venv venv
.\venv\Scripts\activate          # Windows PowerShell
# source venv/bin/activate       # macOS / Linux

pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env             # Then open .env and set APP_ENV=development
```

### 2. Run the Stack with Demo Data

```powershell
# Optional: reset DB to a clean demo state (Jerry Grubb rep, no jobs)
python scripts/db_demo_reset.py

# Start the development server
uvicorn app.main:app --reload --host 127.0.0.1 --port 8001

# Access the app at http://127.0.0.1:8001 using PIN 8471 (admin)
```

> [!IMPORTANT]
> Set `APP_ENV=development` in `.env` before starting. This directs all writes to `data/wickham_dev.db` and protects the production database from accidental modification.

### 3. Before Opening a Pull Request

Run the full validation matrix — all three must pass with zero errors:

```powershell
# 1. Test suite (must be 100% green)
.\venv\Scripts\python.exe -m pytest tests/ -v --cov=app --cov-report=term-missing

# 2. Static type checking (app/core and app/services must be clean)
.\venv\Scripts\python.exe -m mypy app/core app/services

# 3. Linter
.\venv\Scripts\python.exe -m ruff check app/
```

### 4. How to Add a New Building-Code Rule

1. Open the relevant file in `building_codes/` (e.g., `irc_2024.txt`).
2. Add your content using the XML tag format:
   ```xml
   <SECTION_R905_X_X>
   Your rule text here. Keep it concise — this is what gets injected into supplement narratives.
   </SECTION_R905_X_X>
   ```
3. Register the keyword-to-tag mapping in `app/core/code_router.py` inside `KEYWORD_MAP`.
4. Add an assertion in `tests/test_code_router.py` verifying the new tag is indexed and returned for the correct discrepancy keyword.
5. Run `pytest tests/test_code_router.py -v` to confirm.

### 5. How to Add a New Commission Scheme

1. Edit `app/core/job_costing.py` — add a new calculation branch or parameter.
2. If the scheme requires new database columns, add a versioned migration in `app/core/database.py` inside `run_migrations()`.
3. Add a test scenario in `tests/test_commissions_coverage.py` covering the new scheme and any edge cases.
4. Confirm the accounting dashboard template in `app/templates/accounting_dashboard.html` renders the new value correctly.

---



Welcome to the **Wickham Roofing V4 "Wickham Roofing CRM"** engineering repository. This application represents the core operational infrastructure of our business—governing real financial leads, calculating thousands of dollars in material orders, and producing legally binding statutory insurance documentation.

Because this CRM runs natively on local office hardware without third-party cloud SaaS fail-safes, we enforce an exceptionally rigorous standard for software contributions, schema modifications, and test assertions.

---

## 1. The Prime Directive: Zero-Regression Stability

A defect in a standard web application might simply break an interactive button; a mathematical defect in our calculating engine results in severe material short-ordering or financial liability during insurance negotiations.

Therefore, **we strictly enforce a 100% green test baseline across all 282 test assertions**.
- **Every Feature Requires Tests**: No pull request or code commit will be approved without accompanying pytest functions covering successful executions and defensive failure paths.
- **Math Kernel Preservation**: Any changes to `app/services/supplement_engine.py` require exhaustive unit assertions proving determinism across extreme inputs (e.g., zero-clamped dimensions, missing properties, floating-point rounding precision).

---

## 2. Environment Provisioning for Developers

To configure a non-destructive local software engineering workspace:

```powershell
# 1. Clone repository & transition to working folder
git clone https://github.com/blairmichaelg/wickham-roofing-crm.git
cd wickham-roofing-crm

# 2. Initialize an isolated python environment
python -m venv venv
.\venv\Scripts\activate

# 3. Install required development and testing packages
pip install --upgrade pip
pip install -r requirements.txt

# 4. Provision local development environment profile
cp .env.example .env
```

> [!IMPORTANT]
> You must ensure that `APP_ENV` inside your local `.env` file is set strictly to `development` or `dev`. This configures the application to utilize ephemeral developer SQLite tables (`data/wickham_dev.db`), completely protecting production ledgers from modification or backup pollution.

---

## 3. Architecture Rules: Separation of Concerns

If your contribution intersects with AI analysis, estimation algorithms, or background workers, you must observe strict architectural separation:

1. **`app/services/supplement_engine.py` (Pure Deterministic Kernel)**: This domain is dedicated exclusively to functional mathematics. Do not import database drivers, do not initiate network HTTP calls, and do not introduce stateful side effects. It accepts data structural inputs and emits precise numerical outputs.
2. **`app/workers/supplement_processor.py` (Asynchronous Orchestrator)**: This layer manages external complexity. It retrieves items from Redis task queues, hydrates models from SQLite, constructs audit trails, handles file IO, and calls generative AI models for narrative compilation.

**Rule:** Never introduce queue orchestration or database operations into the mathematical kernel, and never perform mathematical calculations directly inside the ARQ orchestrator or UI views.

---

## 4. SQLite Schema & Versioned Migrations

The database layer relies on SQLite running in **WAL (Write-Ahead Logging)** mode with explicit **`BEGIN IMMEDIATE`** transaction limits to prevent TOCTOU race conditions and locking crashes.
- **Modifying the Schema**: Do not manipulate SQLite files directly via external tools (e.g., DBeaver, DB Browser) while Uvicorn servers are running.
- **Migration Architecture**: Schema modifications must be programmed as reproducible, versioned migration structures managed through `app.core.database:run_migrations()`, guaranteeing safe initialization on clean system boots and seamless rolling updates.

---

## 5. Automated Quality & Linter Matrix

Before committing modifications or pushing to remote git servers, you are required to validate your codebase against our three-tier automated testing suite:

```powershell
# 1. Execute exhaustive automated testing suite (must pass 100% cleanly)
.\venv\Scripts\python.exe -m pytest tests/ -v

# 2. Perform static type enforcement inspections
.\venv\Scripts\python.exe -m mypy app/

# 3. Perform code syntax, formatting, and import organization validation
.\venv\Scripts\python.exe -m ruff check app/
```

All commands must complete with **zero errors and zero failing assertions**.

---

## 6. Security Hygiene & Authorization Boundaries

When writing routes or expanding API functionalities, abide by the established Phase 3/Phase 4 security conventions:
- **No Silent Zeros & Token Authentication**: All protected REST and WebSocket routes must bind explicit role verification decorators (`@Depends(verify_admin)`, `@Depends(verify_accounting)`, `@Depends(verify_operations)`, `@Depends(verify_field)`).
- **IDOR Protection**: Any query searching, altering, or extracting records on behalf of field representatives must execute compound ownership evaluations (e.g., verifying `canvasser_rep_id` against JWT claims).
- **Path Traversal Defense**: Never concatenate user-supplied input strings directly into file system path references. Rely on `uuid.UUID` parameter typecasting and invoke `sanitize_download_filename` when serving downloads.
- **Secret & Database Isolation**: Verify that `.env` files and local databases (`*.db`, `*.db-shm`, `*.db-wal`, `tools/*.exe`) remain safely excluded from Git version tracking via `.gitignore`.

Thank you for observing engineering rigor and maintaining the reliability of the Wickham Roofing CRM operating platform!
