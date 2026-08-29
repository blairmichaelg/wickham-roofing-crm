# Wickham Roofing CRM — Field & Offline Runbook

**Wickham Roofing CRM v2.5.8 · Operational Runbook**

This runbook provides emergency operational procedures for Scott and field reps. If the CRM behaves unexpectedly in the field or office, execute the diagnostics below before escalating.

## 1. If the Upload Hangs (Mobile Field App)

- **Symptom**: The mobile browser spins indefinitely after tapping "Submit".
- **Diagnosis**: The Cloudflare tunnel may have expired or crashed.
- **Action**:
  1. Go to the office laptop and check the `logs\tunnel_*.log` file or the `srv_tunnel.ps1` window.
  2. Verify the session status is `online`. If disconnected, terminate the script window and run `scripts\services\srv_tunnel.ps1` to restart the tunnel.
  3. Send the *new* Cloudflare URL to the canvasser if it changed. Note: The mobile app uses `localStorage` caching, so no field data was lost. They just need to reload with the new URL and tap submit again.

## 2. If the Margin is Red (Office Dashboard)

- **Symptom**: The Financials Card shows a red warning banner (Margin < 35%).
- **Diagnosis**: The dynamic math engine triggered a low-margin safety threshold based on your inputs.
- **Action**:
  1. Double-check the "Total Revenue" input vs the "Carrier RCV". Ensure no zeros are missing.
  2. Verify the Supplier PO PDF to ensure the `MaterialBOM` did not over-calculate the waste factor.
  3. If the math is correct, the roof is genuinely unprofitable.

## 3. If the PDF Doesn't Generate

- **Symptom**: Clicking "Download Estimate" or "Supplier PO" returns an error or a broken link.
- **Diagnosis**: The automated math engine (ReportLab) threw an exception during PDF rendering.
- **Action**:
  1. Open a new terminal on the office laptop.
  2. Run the diagnostic tool: `python scripts/analyze_logs.py`
  3. This will scan the `structlog` output for exact stack traces and identify the `job_id` that crashed. Send this trace to engineering.

## 4. If the Database is Locked (State Machine Stuck)

- **Symptom**: The job is stuck in `EV_PARSED` but you know the `MaterialBOM` was calculated.
- **Diagnosis**: An async task crashed halfway, leaving the claim orphaned from the state machine.
- **Action**:
  1. Identify the `job_id` from the dashboard URL.
  2. Use the SRE override tool to force the state machine forward:

     ```bash
     python scripts/recover_job.py <job_id> SUPPLEMENT_SUBMITTED
     ```

  3. Refresh the office dashboard. The job will now be unlocked.

> [!CAUTION]
> Never manually edit the `data/wickham.db` SQLite file with external viewers (like DBeaver) while Uvicorn is running. The Write-Ahead Log (WAL) mode requires FastAPI to maintain the file lock. Use `recover_job.py` instead.

## 5. Naked Lead / Resume & Sign Issues (v2.0.0+)

### "Resume & Sign" button doesn't pre-populate the form

- **Symptom**: Tapping **✍️ Resume & Sign** on a Naked Lead card either does nothing or shows an error toast.
- **Diagnosis**: The `GET /api/field/jobs/{job_id}` endpoint may be returning an auth error or the job ownership check failed.
- **Action**:
  1. Confirm the rep is still logged in (check for the PIN login screen on refresh).
  2. Verify the job's `canvasser_name` in the DB matches the rep's registered name (check via admin dashboard → Field Reps).
  3. If still failing, pull the server log: `logs\fastapi_*.log` and look for the `job_id` and `assert_field_rep_owns_job` entries.

### Unsigned Agreement PDF returns error

- **Symptom**: Tapping **✉️ Unsigned PDF** or **✉️ Email Client** returns a 404 or server error.
- **Diagnosis**: The PDF generator may have failed, or the job_id in the URL is malformed.
- **Action**:
  1. Confirm the job is in `LEAD_CAPTURED` status (admin dashboard).
  2. Try the admin equivalent route: `/api/office/jobs/{job_id}/docs/contingency` — if that works, the issue is the field-rep ownership assertion.
  3. Check `logs\fastapi_*.log` for `generate_contingency_agreement` errors.

### "Email Client" button opens but email field is blank

- **Symptom**: The `mailto:` link fires but the To: address is empty.
- **Diagnosis**: No email address was captured during initial lead intake.
- **Action**: Open the job detail view, use the **Edit Claim Info** modal to add the homeowner's email, save, then retry the Email Client button.

## 6. Voice Note Recording & Upload Diagnostics

- **Symptom**: Tapping **🎙️ Record Voice Note** shows "Microphone access denied or unavailable."
- **Diagnosis**: Mobile browser microphone permissions are blocked or insecure HTTP context is being used.
- **Action**:
  1. Check browser settings (iOS Safari / Chrome) and ensure microphone permission is granted for the domain.
  2. Ensure the app is accessed over HTTPS (Cloudflare tunnel or localhost).
  3. If a voice upload fails during lead submission, the `.webm` audio is preserved in the offline IndexedDB queue and will retry automatically.

## 7. Offline Queue Sync Replay & Error Modal

- **Symptom**: The sync badge displays a red alert or fails during automatic replay.
- **Diagnosis**: Replay payload failed due to network timeout or client validation rejection.
- **Action**:
  1. Tap the sync badge to open the **Sync Error Modal** (`#syncErrorModal`).
  2. Inspect the detailed error message displayed in the dialog.
  3. Tap **🔄 Retry Sync** to re-attempt queue processing immediately.
  4. If the error is a permanent 400 validation issue, note the job address and report to the office admin.

