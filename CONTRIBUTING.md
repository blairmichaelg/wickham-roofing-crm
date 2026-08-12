# CONTRIBUTING TO WICKHAM ROOFING CRM (V4 "WICKHAM ROOFING CRM")

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
