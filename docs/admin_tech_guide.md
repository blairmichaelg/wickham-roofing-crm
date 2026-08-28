# Wickham Roofing — Admin (Tech Admin) Guide

**Wickham Roofing CRM v4 · Admin Role**

This guide covers the full Admin workflow: the pipeline dashboard,
resolving stuck jobs, generating documents, handling carrier approvals
and denials, managing financials, and using the Emergency Override
system. As Admin, you hold the highest level of access in the system.

> [!NOTE]
> **Core Team Access Levels**: The core team bypasses role checks but has distinct permission levels:
> - **Full Access Core (Michael, Scott, Debi)**: Unrestricted full-access across all roles, dashboards, and write operations.
> - **Read-Only Core (Alex Wickham)**: Read-only visibility into all office boards, dashboards, and reports, but blocked from performing any mutating actions (such as creating jobs, editing financials, resolving triage, or running manual overrides).

---

## 1. The Pipeline Dashboard (Kanban)

After logging in, you land on **"Wickham Roofing - Pipeline Orchestrator"**
— your main command center showing every job as a card, organized by
status column. The Wickham Roofing logo appears in the header alongside
the title.

### Navigation

- **👥 Field Reps** — manage canvasser/rep records.
- **⚠ Triage** — jobs stuck waiting for your review.
- **Logout**

### Status columns you'll see

`LEAD_CAPTURED` → `CONTINGENCY_SIGNED` → `EV_ORDERED` → `EV_PARSED` →
`PENDING_OPERATOR_REVIEW` → `SUPPLEMENT_GENERATED` →
`AWAITING_CARRIER_RESPONSE` → `SUPPLEMENT_APPROVED` → `MATERIAL_ORDERED`
→ `INSTALL_SCHEDULED` → `FINAL_INSPECTION` → `INVOICED`

> [!NOTE]
> **Naked Leads** (`LEAD_CAPTURED` with no signature) appear in the first column. These are door-knock contacts captured by field reps who haven't signed yet. They are intentionally separated from signed jobs so they don't clutter the active production pipeline. Click into any `LEAD_CAPTURED` job to see a banner with quick actions: Evidence Grid PDF, downloadable unsigned agreement, and a direct link back to the Field App for the rep to capture the signature.

### Alerts to watch for

- **Red "PENDING_OPERATOR_REVIEW" cards** — a job is blocked and needs
  your attention in Triage (see Section 2).
- **Red "SLA EXCEEDED: X Days" badges** — a carrier has gone silent too
  long and the job needs an escalation letter (see Section 4).

### Clicking into a job

Click any job card to open the **unified job detail view**. This is the
same full-access view shared by Admin, Operations, and Accounting —
all three roles see all financials, margins, documents, and actions.
There is no restriction between these three office roles on job data.

---

## 2. Resolving Stuck Jobs (Operator Triage)

When a measurement report or Statement of Loss parsing can't confidently
extract a required number, the system **refuses to guess** — it
hard-blocks the job into `PENDING_OPERATOR_REVIEW` instead of silently
defaulting to zero. This is intentional and protects you from bad math
downstream.

### How to resolve a stuck job

1. Click **⚠ Triage** from the dashboard.
2. You'll see **"⚠ Operator Triage — Stuck Jobs"** with a list of every
   job currently blocked, each labeled **"PENDING REVIEW."**
3. For each stuck job, you'll see the exact fields the system couldn't
   confidently extract: **Total Area (SF), Pitch (e.g. 6/12), Ridge LF,
   Hip LF, Valley LF, Eaves LF, Rakes LF.**
4. Enter the correct values by hand from the actual report. Fields you
   leave blank will keep their current value (placeholder text: *"Leave
   blank to keep"*).
5. Click **"Resolve & Resume Pipeline."**
   - The button changes to **"Queuing..."** then **"✓ Queued."**
   - You'll see green confirmation text: **"✓ Job queued. Pipeline will
     resume momentarily,"** and the card fades out of the triage list.
   - If something goes wrong, you'll see red text: **"Error: [message]"**
     directly under the button.

If there are no stuck jobs, you'll see a clean green banner:
**"✓ No jobs stuck in review. All clear."**

**Why this matters:** Never guess a value just to clear a job faster.
The whole point of this hard-block is to keep bad numbers out of
supplements and carrier-facing documents. Take the extra minute to
verify against the real report.

---

## 2b. AI Ingestion Safety & Mathematical Verification Gates

To ensure the highest possible reliability of the data entered into the database, the Wickham Roofing CRM implements a strict **AI Safety and Verification Protocol**:

1. **No-Math Prompt Directive**: The Gemini AI is strictly used as a **locator**, not a calculator. The prompts are hard-configured to instruct the LLM never to perform calculations (such as tax calculations, line item RCV additions, or depreciation logic). It must only extract exact printed numbers from the PDF or report.
2. **Deterministic Python-Side Math Verification**: Once the AI extracts the values and constructs a `UniversalClaimAST` object, the Python backend executes strict mathematical validation tests:
   - **Line Item Check**: Every single line item's RCV minus its depreciation must match its ACV exactly (within a ±0.05 tolerance).
   - **Financials Check**: The overall claim's Gross RCV minus the Total Depreciation and Deductible must match the Net Claim exactly (within a ±0.05 tolerance).
   - **Roof Geometry Check**: Physical dimensions and measurements (squares, rakes, eaves, valleys) are verified to be non-negative.
3. **Fail-Safe Operation**: If any mathematical verification fails, or if a negative number is detected where it shouldn't be, the system raises a `ValueError` validation block, stopping the ingestion pipeline and requiring manual review.

---

## 3. Uploading Measurement Reports & Statement of Loss

From a job's detail page, you'll find the **Control Panel** section
with **"Upload Documents."**

### Supported measurement report formats

The system automatically detects whether a measurement PDF is an
**EagleView** or **Hover** report and routes it to the correct parser.
You do not need to specify the format — just upload the PDF and the
system handles detection transparently.

### How to upload

1. Drag and drop the **Measurement Report PDF** (EagleView or Hover)
   and the **Statement of Loss PDF** into their respective drop zones.
   The measurement zone is labeled **"📐 Measurement Report (EagleView
   or Hover)"** and the other **"📋 Statement of Loss."**
2. Click **"Upload & Trigger Pipeline."**
   - The button changes to **"Enqueuing Documents..."**
   - A progress bar appears and updates through stages like
     **"Extracting multimodal data..."** and **"Generating Narrative &
     PDFs..."**
   - When complete, the page reloads automatically.
3. If something fails, you'll see a toast: **"Upload failed: [error]"**
   and the drop zones reappear so you can retry.

### Important current limitation

**Both files are required together every time.** As of this writing,
you cannot re-upload just a corrected Statement of Loss on its own —
the system requires both the Measurement Report and SoL PDFs to be
submitted as a pair, even if only one document was actually wrong. If
you need to correct a single document, re-upload both files together
(using the original measurement report again if it was already correct).
This is a known workflow limitation the team is aware of and may improve
in a future update.

### Common upload errors

- **"Only PDF files are allowed."** — you attached something other than
  a PDF.
- **"Please upload exactly two PDF files (Measurement Report and
  Statement of Loss)."** — you're missing one of the two required
  documents.

---

## 4. Escalating a Stalled Carrier

When a job has exceeded its SLA window with no carrier response, a red
alert appears on the job detail page: **"⚠ Carrier SLA Exceeded (X Days)"**
— where X shows exactly how many days it's been overdue. (You may also
spot this earlier on the Kanban dashboard as **"SLA EXCEEDED: X Days"**
on the job card itself.)

### How to escalate

1. Click **"Generate Escalation Demand Letter."**
2. You'll be asked to confirm: a dialog box appears before anything
   happens, since this generates a real document tied to the carrier
   relationship.
3. Once confirmed, the button changes to **"Generating PDF via
   Gemini..."** then **"Demand Letter Generated ✓."**
4. A download link appears: **"⬇ Download Generated Demand Letter."**

Take a moment before confirming — this creates a formal document, so
make sure escalation is actually warranted before clicking through.

---

## 5. Approving or Denying a Supplement

### Approving

1. Click **"✓ Mark Supplement Approved."**
2. You'll be asked to confirm: **"Mark supplement as APPROVED? This
   will alert Scott and Debi."**
3. Once confirmed, the page reloads and the job moves forward in the
   pipeline.

### Denying (triggers AI Rebuttal)

1. Paste the carrier's denial text into the **"Carrier Denial Text
   (paste from email)"** box.
2. Click **"✗ Mark Denied — Generate AI Rebuttal."**
3. If the text box is empty, you'll see a **red toast notification** in
   the corner: **"Please paste the carrier denial text first."**
4. You'll then be asked to confirm, since this action consumes AI
   processing time and generates a real rebuttal letter: a confirmation
   dialog appears before the request is sent.
5. Once confirmed, a toast notification appears instead of a blocking
   popup, and the system automatically checks every few seconds for
   completion — you don't need to guess when it's done or manually
   refresh. Once the rebuttal is ready, the page reloads on its own
   with the rebuttal available to download. If generation genuinely
   fails, the system detects that too and reloads to reflect the
   failed state rather than leaving you waiting indefinitely.

**Take your time before denying.** This isn't reversible, and it kicks
off real AI generation — make sure the denial text is accurate and
complete before confirming.

---

## 6. Reviewing Financials

On the job detail page, under **"Financials,"** you can view and edit:

- Revenue
- Materials
- Labor
- Carrier RCV
- Deductible
- ACV Payment
- Recoverable Dep.

Click **"Save Financials"** when done. You'll see a confirmation toast:
**"Financials saved!"** If something goes wrong, the toast will show
the specific error message instead.

---

## 7. Document Vault

Every job has a **Document Vault** for storing artifacts beyond the
core measurement report/SoL pipeline documents.

### How to upload

1. Drag a file into the vault drop zone.
2. On success: **"Document uploaded successfully!"**
3. On failure: the toast will show the specific error.

### Downloadable documents available per job

- **⬇ Download QBO CSV**
- **Notice of Cancellation (PDF)**
- **Evidence Grid (PDF)**
- **⬇ Download Supplement PDF**
- **⬇ Download Generated Demand Letter** (once escalated)
- **⬇ Download Rebuttal Letter** (once a denial has been processed)

### Document visibility

Documents in the system have a visibility level:

- **`field_safe`** — visible to field reps (e.g., measurement reports,
  contingency agreements, photos).
- **`office_only`** — visible only to Admin, Operations, and Accounting
  (e.g., QBO exports, financial documents).

Field reps can only see and download `field_safe` documents. Office
roles (Admin, Operations, Accounting) can see and download all documents
regardless of visibility.

---

## 8. Emergency Admin Override

This is your highest-privilege tool: the ability to **force** a job
into any status, bypassing the normal pipeline entirely. Use this only
when a job is genuinely stuck and cannot be resolved through Triage or
the normal workflow buttons above.

### How to use it

1. On the job detail page, scroll to the bottom to the red-bordered
   **"⚠ Admin Override (Emergency Use Only)"** panel and click to expand it.
2. Read the warning text: *"This forcefully changes the job's status,
   bypassing the normal pipeline. Use only when a job is stuck and
   cannot be resolved through Triage or normal workflow buttons. This
   action is logged."*
3. Select the **New Status** you want to force the job into from the
   dropdown.
4. Type a clear **Reason for Override** — this field is required. You
   cannot submit without it, and the system will reject the request
   even if attempted directly through the API without a reason.
5. Click **"Force Status Change."**
6. You'll get one final confirmation dialog summarizing exactly what
   you're about to do and the reason you gave, before anything happens.
7. On success, you'll see **"✓ Override applied. Reloading..."** and
   the page refreshes.

### Why the reason field matters

Every override is permanently logged with the reason you provide. This
isn't just a formality — it's your audit trail. If you or anyone else
ever needs to understand why a job's status doesn't match its normal
history, this note is the explanation. Always write something specific
and useful, such as:

- "Carrier confirmed verbal approval by phone; documentation pending. Advancing manually to avoid delay."
- "Duplicate job created during offline sync error; forcing to CLOSED to remove from active pipeline."

Avoid vague notes like "fixing" or "testing."

### When to use Override vs. Triage

- **Use Triage** when a job is stuck specifically because of missing
  measurement data from report parsing.
- **Use Override** only for everything else — genuinely stuck jobs, data
  entry mistakes, duplicate records, or unusual situations the normal
  workflow buttons can't handle.

**This is not reversible in a simple sense.** You can override again to
a different status, but any side effects the normal pipeline would have
triggered along the way (notifications, document generation, etc.) will
not happen automatically. Use this sparingly and document your reasoning
clearly every time.

---

## FAQ

**Q: A job is stuck in PENDING_OPERATOR_REVIEW. What do I do?**
Go to **⚠ Triage**, find the job, enter the missing measurement values
by hand from the real report, and click **Resolve & Resume Pipeline.**

**Q: Can I re-upload just a corrected Statement of Loss?**
Not currently — you need to re-upload both the Measurement Report and
Statement of Loss PDFs together, even if only one was wrong. Use the
same measurement report file again if it was already correct.

**Q: Does the system support Hover measurement reports?**
Yes — the system automatically detects whether a PDF is EagleView or
Hover and routes it to the correct parser. You do not need to specify
the format.

**Q: I clicked Deny by mistake. Can I undo it?**
No — denial triggers real AI rebuttal generation immediately after you
confirm. That's why there's a confirmation dialog now; always double-
check the denial text before confirming.

**Q: What's the difference between Triage and Admin Override?**
Triage fixes a specific, known problem (missing measurements from
parsing). Override is a blunt instrument for anything else — use it
only when nothing else applies.

**Q: Do I need a reason to use Admin Override?**
Yes, always. The system will not let you submit an override without
one, and this reason becomes part of the permanent audit trail.

**Q: How do I know an AI Rebuttal actually generated?**
After confirming a denial, the system checks automatically in the
background every few seconds and reloads the page once it's either
ready or has genuinely failed — you don't need to manually refresh or
guess how long to wait.

**Q: What if I need to escalate a job but I'm not sure it's warranted yet?**
Take the extra moment before confirming — escalation generates a real
demand letter tied to the carrier relationship, so it's worth being
certain first.

**Q: Can Scott (Operations) and Debi (Accounting) see the same job
data I see?**
Yes — all three office roles (Admin, Operations, Accounting) share the
same unified job detail view with full access to financials, margins,
and all documents. The only role with restricted document visibility
is field reps/canvassers, who can only see `field_safe` documents.

---

## 9. Storm Activity Monitor Ingestion Worker

The Storm Activity Monitor feature runs as a background process to ingest Local Storm Reports (LSR) from the National Weather Service (NWS) ArcGIS feed.

### Ingestion Schedule
- **Schedule**: The storm ingestion worker runs periodically based on the config setting `STORM_INGEST_INTERVAL_MINUTES` (defaults to every `15` minutes).

### Configuration Options
The Storm Activity Monitor's ingestion and alerting thresholds are controlled by the following environment variables (defined in `app/config.py`):
1. `STORM_OFFICE_LAT` (default: `30.8766`): Latitude of the central office center around which storms are monitored.
2. `STORM_OFFICE_LON` (default: `-84.1994`): Longitude of the central office center around which storms are monitored.
3. `STORM_INGEST_RADIUS_MILES` (default: `50.0`): The radius in miles around the office center within which storm events are ingested/saved to the database.
4. `STORM_ALERT_RADIUS_MILES` (default: `30.0`): The radius in miles around the office center within which events must fall to trigger active WebSocket alerts to users.
5. `STORM_ALERT_MIN_HAIL_INCHES` (default: `1.0`): The minimum hail size (in inches) required to trigger a storm alert.
6. `STORM_ALERT_MIN_WIND_MPH` (default: `50.0`): The minimum wind speed (in mph) required to trigger a storm alert.

### Verifying Worker Health
To confirm the worker is running and processing correctly:
1. Check the background service worker logs at `logs/srv_worker.log`.
2. Inspect log outputs for entries matching `ingest_storm_events_started` and `ingest_storm_events_success`. For example, a successful run will log:
   `ingest_storm_events_success: processed=X, inserted=Y, alerts_count=Z`

---

## 10. Sales Pipeline & Canvassing Intelligence Widgets

Admin and office users have access to two new widgets directly in the main Office Control Center dashboard:

### 1. Canvassing Targets (Storm Radar Monitor)
- **Ranked Target ZIPs**: Below the recent storm event alerts, the Storm Activity Monitor card displays a ranked list of "Canvassing Targets".
- **Adjustable Time Window**: Toggles lookback periods (24h, 72h, 168h) dynamically from the admin header select control.
- **Explainable Scoring**: Target areas (defined by location and ZIP code) are automatically ranked based on `severity_score` calculated during ingestion. Priority labels (`🔥 High`, `⚡ Medium`, `🟢 Low`) are displayed alongside a detailed description explaining the score (e.g. `1.75″ hail · 60 mph wind · latest Aug 28`).
- **Tornado/Severe Badges**: High-priority storm targets are highlighted with special badges to help the team focus canvassing efforts where severe damage is most likely.

### 2. Sales Pipeline Widget
- **Pipeline Snapshot**: A dedicated collapsible "Sales Pipeline" widget is located below the Storm Activity Monitor.
- **Stage Breakdown**: Displays live counts of jobs across key pipeline stages.
- **Rep Performance**: Displays a breakdown of leads, contingency agreements signed, and contracts secured per sales representative.
- **Deterministic Speed-to-Lead tracking**: Computes and displays the average time (in hours) it takes for a newly captured lead to advance to its first progression milestone.

---

*This guide reflects the Admin workflow as of version `2.4.1`. Includes support for non-blocking 'Naked Lead' field intake (`LEAD_CAPTURED`), pre-contract Evidence Grid pitch generation, unsigned contingency agreement PDF download/email, field-app-based signature resumption flow, NWS-integrated Storm Activity Monitor, ranked Canvassing Targets, the Sales Pipeline dashboard widget, and the read-only core role classification for Alex Wickham.*
