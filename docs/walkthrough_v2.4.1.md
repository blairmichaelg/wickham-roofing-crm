# Release Walkthrough — Version 2.4.1

## Objective & Scope

This release hardens the authorization boundaries of the Wickham Roofing CRM by splitting the core team name-bypass into two tiers: full-access (Michael, Scott, Debi) and read-only (Alex Wickham). It also resolves router import coupling to prevent transitive load failures, removes a redundant help tab in the templates, and corrects all help guides to reflect these operational controls.

## Exact Files Changed

- **[`app/core/templates.py`](file:///c:/Users/Michael/projects/wickham-roofing-crm/app/core/templates.py)**: Shared Jinja2Templates instance.
- **[`app/server.py`](file:///c:/Users/Michael/projects/wickham-roofing-crm/app/server.py)**: Router setup and shared templates attachment.
- **[`app/api/office_routes.py`](file:///c:/Users/Michael/projects/wickham-roofing-crm/app/api/office_routes.py)**: Office templates and inspection imports.
- **[`app/api/operations_routes.py`](file:///c:/Users/Michael/projects/wickham-roofing-crm/app/api/operations_routes.py)**: Operations templates import.
- **[`app/api/auth.py`](file:///c:/Users/Michael/projects/wickham-roofing-crm/app/api/auth.py)**: Core team set split, `is_*_or_core` helpers, dependency request threading, and read-only HTTP method validation.
- **[`app/services/field_access.py`](file:///c:/Users/Michael/projects/wickham-roofing-crm/app/services/field_access.py)**: Field job ownership bypass for Alex Wickham.
- **[`app/templates/help.html`](file:///c:/Users/Michael/projects/wickham-roofing-crm/app/templates/help.html)**: Removed Debi's Onboarding tab, content pane, and javascript references.
- **[`docs/admin_tech_guide.md`](file:///c:/Users/Michael/projects/wickham-roofing-crm/docs/admin_tech_guide.md)**: Updated version and core access descriptions.
- **[`docs/accounting_guide.md`](file:///c:/Users/Michael/projects/wickham-roofing-crm/docs/accounting_guide.md)**: Consolidates Debi's onboarding content and version info.
- **[`docs/operations_guide.md`](file:///c:/Users/Michael/projects/wickham-roofing-crm/docs/operations_guide.md)**: Core role details and version info.
- **[`docs/security_tasks.md`](file:///c:/Users/Michael/projects/wickham-roofing-crm/docs/security_tasks.md)**: Core Team Access section.
- **[`docs/testing.md`](file:///c:/Users/Michael/projects/wickham-roofing-crm/docs/testing.md)**: Added `test_core_rbac_split.py` to testing map, bumped test totals.
- **[`README.md`](file:///c:/Users/Michael/projects/wickham-roofing-crm/README.md)**: Updated version and tests badges.
- **[`pyproject.toml`](file:///c:/Users/Michael/projects/wickham-roofing-crm/pyproject.toml)**: Bumped version to `2.4.1`.
- **[`CHANGELOG.md`](file:///c:/Users/Michael/projects/wickham-roofing-crm/CHANGELOG.md)**: Added release notes section for v2.4.1.
- **[`tests/test_core_rbac_split.py`](file:///c:/Users/Michael/projects/wickham-roofing-crm/tests/test_core_rbac_split.py)**: Core split unit test suite.

---

## RBAC Behavior (Before vs After)

| User / Role | Request Method | Before | After |
|---|---|---|---|
| **Michael / Scott / Debi** | `GET` / `POST` / `PUT` / `DELETE` | Allowed bypass on all routes | **Allowed bypass (Full Access Core)** |
| **Alex Wickham** | `GET` / `HEAD` / `OPTIONS` | Allowed bypass on all routes | **Allowed bypass (Read-Only Core)** |
| **Alex Wickham** | `POST` / `PUT` / `PATCH` / `DELETE` | Allowed bypass on all routes | **Blocked (403 Forbidden - Read-Only)** |
| **Standard Rep (Field)** | `GET` / `POST` (Office Routes) | Blocked (403 Forbidden) | **Blocked (403 Forbidden)** |
| **Standard Rep (Field)** | `GET` / `POST` (Owned Jobs) | Allowed (ownership bounds) | **Allowed (ownership bounds)** |

---

## Help Page Changes (Before vs After)

- **Before**: Renders five tabs: "Admin Guide", "Accounting Guide", "Debi's Onboarding", "Operations Guide", "Field Guide", "Field Runbook".
- **After**: Renders four tabs for core/office roles: "Admin Guide", "Accounting Guide", "Operations Guide", "Field Guide", "Field Runbook". The "Debi's Onboarding" tab and corresponding HTML/JS content sections have been entirely removed and consolidated into the main Accounting Guide section. `docs/debi_onboarding_guide.md` remains on disk.

---

## Tests Run & Verification Results

### Targeted Regression & New Checks
- **Command**: `python -m pytest tests/test_core_rbac_split.py tests/test_rbac_hardening.py tests/test_ui_contracts.py -v`
- **Result**: All passing.

### Full Test Suite Pass
- **Command**: `python -m pytest`
- **Result**: **418 passed in 91.59 seconds (100% Success)**.

### Static Code Validation
- **Ruff**: `ruff check app/` -> `All checks passed!`
- **Mypy**: `python -m mypy app/core app/services` -> `Success: no issues found in 64 source files`

---

## Deferred Findings

None. All constraints and features were fully implemented and verified without issue.
