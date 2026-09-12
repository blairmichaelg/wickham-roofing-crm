# Security Authorization Boundaries

This document summarizes the authorization boundaries and security enforcements implemented across the `wickham_crm` API infrastructure as of Phase 3 Security Hardening.

## Office Routes (`/api/office`)
**Dependency:** `verify_admin` (unless otherwise noted)
- **Document Management** (`/supplement_docs`, `/docs/upload`): Requires Admin role. Mitigated against Path Traversal vulnerabilities (downloads are strictly sanitized).
- **Material & Production** (`/material_order`, `/production`): Requires Admin role. ARQ enqueuing endpoints are rate-limited via an in-memory sliding window (3 requests / 10 seconds per IP) to prevent Denial of Service.
- **Triage & Escalation** (`/admin/triage/{job_id}/resolve`, `/escalate`): Requires Admin role. Background queue triggers are rate-limited.
- **Accounting** (`/accounting/jobs/{job_id}/toggle-payment`): Overridden to require **`verify_accounting`** role explicitly. Rate-limited.

## Field Routes (`/api/field`)
**Dependencies:** `verify_field` AND `assert_field_rep_owns_job`
- **Data Access & Mutability**: A mandatory ownership check enforces that the `canvasser_rep_id` on the target job matches the field rep's ID embedded in their JWT.
- **Exception**: Admins (via JWT claims) can bypass the field rep ownership check for auditing and fallback intervention.
- **Resume Supplement** (`/resume-supplement`): Validates ownership, explicitly injects the user's role into the background ARQ task, and is protected by the sliding window rate limiter.

## Background Pipelines (ARQ Workers)
- **Role Scoping**: Background task `process_supplement_event` strictly verifies the injected context role. Execution halts immediately with a 403-equivalent status if the role is missing or not in `{"admin", "operations"}`.
- **Code Citations**: Job resumes properly fetch necessary IBC/IRC building codes instead of silently injecting empty references.

## Core Services
- **Field Rep PINs**: The "No Silent Zeros" mandate is enforced. All field rep PINs are stored securely via `bcrypt` hashing, replacing legacy plaintext configurations.
- **JWT Verification**: System is pinned exclusively to the `HS256` symmetric algorithm to mitigate algorithm confusion attacks. `None` algorithms are actively rejected.

## Core Team Access
- **Full Access Core** (`{"michael", "scott", "debi"}`): Bypasses all role boundaries on any endpoint with full write/mutate access.
- **Read-Only Core** (`{"alex wickham"}`): Allowed only `GET`, `HEAD`, and `OPTIONS` operations across all admin/office endpoints. Blocked from any mutating operations (`POST`, `PUT`, `PATCH`, `DELETE`).

## Georgia Statutory & Legal Compliance (O.C.G.A. § 10-1-393.12 & SB 201)
- **5-Day Post-Denial Invoicing Lock**: Enforced server-side in `create_invoice_route`. Prevents invoice generation on insurance jobs until 5 full business days have elapsed since a `CLAIM_DENIED` status event. Emergency services (tarping) are exempt.
- **Assignment of Benefits (AOB) Prohibition**: Under Georgia SB 201 (O.C.G.A. § 33-24-59.28), post-disaster residential roofing contracts cannot assign insurance proceeds or rights to contractors. Scanned and rejected deterministically via `app/services/compliance.py`.
- **Statutory Cancellation Formatting**: Mandatory boldface ≥ 10-point font disclosures on insurance contracts and detachable duplicate Notice of Cancellation forms.
## Dependency Security & Cryptographic Stability
- **bcrypt Version Pin (`bcrypt==3.2.2`)**: Strictly pinned to `3.2.2` in `requirements.txt`. `bcrypt >= 5.0.0` changed behavior from silently truncating passwords over 72 bytes to raising `ValueError`. Under `passlib` (which expects the legacy truncation behavior during salt generation / hashing), upgrading to `>= 5.0.0` causes unhandled 500 exceptions across all authentication routes. This pin must not be removed or upgraded without an explicit migration away from `passlib`.

## JWT Revocation & Active Session Blacklist
- **Revocation Table (`revoked_tokens`)**: Stores revoked JWT unique IDs (`jti`), revocation timestamps, and optional expiry timestamps.
- **Edge Middleware & Dependency Inspection**: Both `JWTRevocationMiddleware` and `decode_token` check incoming token `jti` claims against the `revoked_tokens` table. Any request bearing a revoked token is immediately rejected with HTTP 401 ("Token has been revoked") without waiting for the 12-hour expiration window.
- **Admin Session Revocation API**: Administrators can revoke an active session via `POST /api/admin/auth/revoke` passing either `{"jti": "<uuid>"}` or `{"token": "<jwt_string>"}`.
- **PIN Isolation Guarantee**: Session revocation operates exclusively on issued JWT metadata (`jti`), completely isolated from field rep PINs, PIN hashing algorithms, and PIN verification logic.

_End of document._


