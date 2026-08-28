# Wickham Roofing — Operations Guide

**Wickham Roofing CRM v4 · Operations Role (Scott)**

This guide covers the Operations Command board: tracking material
orders, confirming deliveries, scheduling install crews, and reviewing
full job details. Your role is focused on keeping jobs moving through
the production pipeline once a supplement has been approved.

---

## 1. Logging In

1. Open the app and enter your assigned 4-digit PIN.
2. On success, you're taken to **"Operations Command"** — your main
   working screen. The Wickham Roofing logo appears in the header.

To log out, click **Logout** in the top-right corner of the dashboard.

> [!NOTE]
> **Core Team Access Levels**:
> - **Full Access Core (Michael, Scott, Debi)**: Unrestricted full-access across all roles, dashboards, and write operations.
> - **Read-Only Core (Alex Wickham)**: Read-only visibility into the Operations Command board and all job profiles, but blocked from performing mutating actions (such as marking materials ordered/on site, or scheduling installations).

---

## 2. Understanding the Operations Board

The board is organized into three panels, each representing a stage a
job passes through after its supplement is approved and before
installation begins.

### Panel 1: Alert — Materials Needed
**"Supplement approved. Awaiting material order placement."**

Jobs appear here the moment a supplement is approved and no material
order has been placed yet. This is your cue to place the order with
your supplier.

- Click **"Mark Ordered"** once you've placed the order.
- If no jobs are waiting, you'll see: **"No pending material orders."**

### Panel 2: Awaiting Delivery
**"Materials ordered. Waiting for arrival on site."**

Jobs appear here once you've marked materials as ordered, but before
they've physically arrived on site.

- Click **"Mark On Site"** once the materials have actually arrived at
  the job location.
- If no jobs are waiting, you'll see: **"No materials awaiting delivery."**

### Panel 3: Ready to Build
**"Materials on site. Awaiting crew assignment."**

Jobs appear here once materials have arrived. This is where you assign
a crew and set an install date.

- If no jobs are ready, you'll see: **"No jobs ready to build."**

### Viewing full job details
Every job card on the board includes a **"View Details →"** link that
opens the full job detail page. This gives you complete access to all
job information — including financials, margins, measurement data, all
documents, and supplements. Admin, Operations, and Accounting all share
this same unified detail view with no restrictions between these three
office roles.

---

## 3. Marking Materials as Ordered

1. Find the job under **"Alert: Materials Needed."**
2. Click **"Mark Ordered."**
3. You'll be asked to confirm: **"Confirm materials have been ordered
   for this job?"**
4. Once confirmed, the page reloads and the job moves into the
   **Awaiting Delivery** panel.

If something goes wrong, you'll see a popup: **"Error updating
status."** If this happens, try again, and contact the Tech Admin if
it persists.

---

## 4. Marking Materials as On Site

1. Find the job under **"Awaiting Delivery."**
2. Once the materials have physically arrived, click **"Mark On Site."**
3. You'll be asked to confirm: **"Confirm materials have arrived on
   site for this job?"**
4. Once confirmed, the page reloads and the job moves into the
   **Ready to Build** panel, ready for crew scheduling.

**Important:** Only click this once materials have actually arrived —
this step drives the entire pipeline forward, and other roles rely on
this status being accurate. Marking it too early can cause a crew to
be scheduled before materials are actually on hand.

---

## 5. Scheduling a Crew

1. Find the job under **"Ready to Build."**
2. Fill in:
   - **Assign Crew** — type the crew name (e.g. "Alpha Team").
   - **Install Date** — pick the date using the date picker.
3. Click **"Schedule Installation."**
4. You'll be asked to confirm with your exact entries, for example:
   **"Schedule Alpha Team for install on 2026-07-28?"**
5. Once confirmed, the page reloads once the job is scheduled.

Double-check the crew name and date before confirming — the confirmation
message shows exactly what you typed, so use that as a chance to catch
typos before committing.

If something goes wrong, you'll see a popup: **"Error scheduling
crew."** Double-check the crew name and date, then try again.

---

## 5b. Material & Geometry Data Integrity

To prevent construction errors and order mistakes, the CRM performs strict verification on all measurement data and material properties:
- **Geometry Non-Negativity Checks**: All physical dimensions (squares, rakes, eaves, valleys) are strictly validated to be non-negative values. If a negative value is detected during parsing or triage, the system blocks the ingestion process to prevent downstream ordering errors.
- **Zero-Math AI Ingestion**: The system forces the AI to only locate and extract written text, preventing the LLM from trying to compute or estimate physical counts itself. All materials calculations, waste factors, and bill of materials (BOM) logic are handled deterministically by the Python backend.

---

## 6. What You Cannot Do (By Design)

For security and role isolation, your access is intentionally limited:

- You cannot access the Admin control panel, Triage, or the Emergency
  Override tool.
- You cannot access Field rep intake screens.
- You cannot create or manage field rep accounts.

**What you CAN do:** Via the "View Details →" links on your board, you
have full read access to any job's detail page, including financials,
margins, measurement data, and all documents. This is the same unified
view that Admin and Accounting see — there is no restriction between
these three office roles on job data.

If you try to access a restricted admin-only page directly (e.g.,
Triage), you'll be blocked with a security error — this is expected.

---

## FAQ

**Q: I marked materials as ordered by mistake. Can I undo it?**
There is a confirmation step before this action fires, so double-check
before confirming. If you've already confirmed in error, contact the
Tech Admin to correct the job's status.

**Q: A job isn't showing up on my board at all. What's wrong?**
Jobs only appear once their supplement has been approved. If a job
should be here but isn't, check with the Tech Admin — it may still be
in the supplement/carrier approval stage.

**Q: What's the difference between "Mark Ordered" and "Mark On Site"?**
"Mark Ordered" means you've placed the order with your supplier. "Mark
On Site" means the materials have physically arrived at the job
location and crew scheduling can begin. Don't mark something on site
until it's actually there — this drives the real-world production
schedule and downstream crew assignment.

**Q: Can I see a job's financials or documents?**
Yes — click the **"View Details →"** link on any job card. You'll see
the full job detail page with all financials, margins, documents, and
supplements. This is the same view that Admin and Accounting see.

**Q: What happens if I try to access an admin page directly?**
You'll be blocked with an access error. This is intentional — while
you have full access to job details, admin-specific tools like Triage
and Emergency Override are restricted to the Admin role.

**Q: I typed the wrong crew name or date. What happens?**
Before anything is saved, you'll see a confirmation popup showing
exactly what you entered — for example, "Schedule Alpha Team for
install on 2026-07-28?" Use that moment to check your entry. If you
already confirmed with a mistake, contact the Tech Admin to correct it.

---

*This guide reflects the Operations workflow as of version `2.4.1` (2026-08-28).
If new panels, buttons, or workflows are added in future updates, this
guide should be reviewed and updated to match.*
