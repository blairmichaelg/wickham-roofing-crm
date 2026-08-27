# Wickham Roofing — Canvasser & Field Rep Guide

**Wickham Roofing CRM v4 · Field Role**

This guide covers everything a canvasser or field sales rep needs to know
to use the Wickham Roofing field app: logging in, creating leads, taking
photos, capturing signatures, reviewing your past jobs and documents,
and understanding how offline mode and error recovery work.

---

## 1. Logging In

1. Open the app on your phone or tablet. You'll see the **Wickham Roofing**
   login screen with the company logo and the label **"Wickham Roofing CRM v4."**
2. Tap **"Enter Your PIN"** and use the on-screen keypad (0–9, ⌫) to type
   your assigned 4-digit PIN.
3. If your PIN is wrong, you'll see **"✕ Incorrect PIN. Try again."**
   Re-enter carefully — don't guess repeatedly.
4. On success, you're taken straight to the **New Lead** screen.

**Security reminder:** Your PIN is yours only. Never share it, and never
let someone else work inside your session. The system checks your identity
on every job you touch — using someone else's PIN can cause job ownership
issues down the line.

---

## 2. Creating a New Lead

When you're with a homeowner and ready to start a job, you'll fill out the
**New Lead** form.

### Step-by-step

1. **Job Type** — Choose either:
   - **Insurance Restoration (Contingency)** — for storm/insurance claims.
   - **Retail Cash** — for out-of-pocket jobs.
2. **Homeowner Name** — Use their legal or billing name.
3. **Address Line 1, City, State, Zip** — Enter the property address manually. Ensure all street details, city names, and zip codes are typed correctly.
4. **Phone** — Required.
5. **Date of Loss (Optional)** — If the homeowner knows when the storm
   damage happened, enter it here.
6. **Property Photos (up to 15)** — Tap **📷 Tap to add photos** to open
   your camera or photo library.

---

## 3. Taking and Reviewing Photos

- You can add **up to 15 photos** per job. A counter shows **"X / 15 photos"**
  as you go.
- **If you try to add more than 15 at once:** the app keeps the first
  ones that fit and gives you two clear warnings so you don't miss it:
  - A toast message: *"Maximum of 15 photos allowed. 5 photo(s) were
    not added."*
  - The photo counter itself turns **red and bold** for about 6 seconds,
    showing something like *"15 / 15 photos — 5 rejected!"*
  
  If you see either of these, go back and manually pick which extra
  shots matter most, then remove a less important photo to make room.
- **Reviewing your photos:** Tap any thumbnail to open it full-screen and
  check focus and lighting before submitting. If a photo is blurry or
  unclear, tap the **✕** on that thumbnail to remove it and retake it.

### Photo tips

- Capture every roof slope, damage area, flashing, and accessory (vents,
  skylights, chimneys).
- Shoot in good light. Avoid heavy shadows or backlighting.
- Take a wide shot of each side of the house, then close-ups of damage.

---

## 4. Reviewing the Contingency Agreement & "Naked Lead" Workflow

Field reps can submit a **Naked Lead** (Name, Address, Phone, and optional Date of Loss/Insurer) **without requiring an immediate signature or photos**.

### Why Use Naked Leads?

- **Door-Knock Lead Capture**: Quickly store contacts on-the-go after an initial door-knock.
- **Sales Presentation & Pitching**: Capture property details, return to run NOAA storm searches, and generate the **Inspection Evidence Grid PDF** to present storm damage proof to the homeowner *before* asking them to sign.
- **Durable Persistence**: All naked leads attach to your rep login and are visible to the core management team without clogging active production columns.

### When the Homeowner Is Ready to Sign

If the homeowner accepts the terms during intake or during a follow-up visit:

1. Review the **Insurance Contingency Agreement Summary** with the homeowner (including deductible warnings under O.C.G.A. § 33-24-59.27).
2. Check the acknowledgment box: *"Homeowner accepts terms & conditions"*.
3. Draw the signature on the canvas and tap **Lock Signature** or **✓ Save E-Signature**.

---

## 5. Capturing Signatures (Initial Intake or Follow-Up)

- **During Lead Creation**: If you lock a signature during initial intake, the app automatically generates the signed agreement PDF and advances the job to **Agreement Signed** (`CONTINGENCY_SIGNED`).
- **Naked Lead Follow-Up**: If you save a lead without a signature, it is saved as `LEAD_CAPTURED` with a distinct **📝 Naked Lead — Unsigned** amber badge in your **My Recent Jobs** list.
- **Signing a Naked Lead Later**: From **My Recent Jobs**, tap the **✍️ Resume & Sign** button. This loads the existing homeowner details directly back into the intake form so you can walk through the full contingency agreement on-screen and capture the signature — exactly the same way as a fresh intake, just pre-populated.

---

## 6. Submitting and Following Up on Leads

When you tap **Save Lead** / **Submit Lead**, you'll see a progress modal with messages like:

- "Creating Lead..."
- "Uploading Photo X of Y..." (if photos attached)
- "Generating Contract..." (if signature attached)

If everything succeeds, you'll see **"Lead Captured"** and
**"The office has been notified."** — the job is now live in the system.

---

## 7. My Recent Jobs & Documents

Below the New Lead form, you'll find a **"My Recent Jobs"** section
that automatically loads your most recent jobs (up to 50, newest first).

### What you see

Each job card shows:

- **Homeowner name** and **address**
- **Status badge** — amber **📝 Naked Lead — Unsigned** or green **✅ CONTINGENCY_SIGNED** etc.
- Action buttons (see below)

### Job Card Action Buttons

| Button | What It Does |
| --- | --- |
| **Docs ↓** | Expands to show all field-safe documents for the job |
| **📄 Evidence Grid** | Downloads the Evidence Grid storm findings PDF (great for pitching to homeowners) |
| **✉️ Unsigned PDF** *(Naked Leads only)* | Downloads a printable unsigned Contingency Agreement PDF |
| **✉️ Email Client** *(Naked Leads only)* | Opens your email client pre-filled with a link to the unsigned agreement |
| **✍️ Resume & Sign** *(Naked Leads only)* | Pre-populates the intake form with saved data so you can capture the signature |
| **⚡ View Overview →** *(Signed jobs)* | Opens the full job detail in the office portal |

### Viewing your documents

1. Tap **"Docs ↓"** on any job card.
2. The card expands to show all **field-safe** documents for that job —
   this includes your uploaded photos, measurement reports (EagleView or
   Hover), and the signed contingency agreement PDF.
3. Tap **"View"** next to any document to open or download it.
4. Tap **"Hide Documents ↑"** to collapse the list.

### What you will NOT see

You will **never** see office-only documents such as:

- Financial calculations or QBO exports
- Commission statements
- Internal supplement narratives
- Carrier correspondence or demand letters

This is enforced at the server level — even if you know a document's
ID, the system will block access with a **403 Forbidden** response.
This is by design to protect sensitive business and financial data.

---

## 8. If Something Goes Wrong Mid-Submission (Server Errors)

Sometimes the server itself rejects a step — for example, a photo file is
too large, or there's a temporary server issue. This is different from
having no signal (covered in Section 9).

### What you'll see

If a photo or the signature upload fails due to a server-side error, the
app **stops the submission** and shows a message like:

> *"Photo 3 failed: [error detail] — Tap Submit Lead again to retry the
> remaining steps."*

### What to do — and why it's safe and efficient

**Just tap Submit Lead again.** The app is smart about retries:

- It will **not** create a duplicate job — it remembers the lead was
  already created and picks up exactly where it left off.
- It will **not** re-upload photos that already succeeded. If photo 3
  out of 15 failed, retrying only re-attempts photo 3 onward — photos
  1 and 2 are already safely on the server and won't be sent again.
- If all your photos succeeded but the signature failed, retrying only
  re-sends the signature — none of your photos get re-uploaded.

This means you don't need to worry about wasting time or mobile data
re-sending things that already worked. The app only retries the exact
step that failed.

### If it keeps failing

If retrying doesn't work after 2–3 attempts, note the job address and
contact the office — there may be an issue with a specific file (for
example, a corrupted photo) that needs a fresh photo taken instead.

---

## 9. Working Offline (No Signal / Weak Signal)

The app is built to handle bad service in the field, whether you have zero
signal or a connection that drops partway through a submission.

### If you're fully offline when you submit

You'll see: **"Offline Mode: Lead, photos, and signature saved locally.
Will sync automatically when connection returns."**

### If your connection drops mid-submission

The app saves **everything** — the lead info, all photos, and the
signature — as one complete package, tied to the job that was already
created if it got that far. You do not need to worry about photos or
signatures getting lost, and you will not end up with a duplicate job
once it syncs.

### How you know something is still waiting to sync

Look for a small badge in the corner of the screen showing **"X pending
sync."** This badge stays visible — it does not disappear after a few
seconds like the offline banner does — so you always know if work is
still waiting to reach the office.

- **Yellow badge** — normal. Items are waiting for a connection and will
  sync automatically. The count goes down as items succeed.
- **Red badge saying "FAILED — contact office"** — something is
  permanently stuck (for example, a corrupted photo file that the server
  keeps rejecting). This will not fix itself by waiting. **Contact the
  office right away** so they can help resolve that specific job.

### What to do if you see a pending sync badge

- **Yellow:** No action needed beyond getting to a signal area. It syncs
  automatically.
- **Red:** Note the job/address shown and contact the office immediately
  — this item needs manual attention and won't clear on its own.

---

## 10. Common Situations & What They Mean

| What you see | What it means | What to do |
| --- | --- | --- |
| "✕ Incorrect PIN. Try again." | Wrong PIN entered | Re-enter your PIN carefully |
| "Photo X failed: [error] — Tap Submit Lead again to retry" | A server error stopped the submission | Tap Submit Lead again — it's safe, skips completed photos, and won't duplicate the job |
| "Maximum of 15 photos allowed..." toast + red "X rejected!" counter | You selected more than 15 photos | Remove a less important photo, then re-add the one you need |
| "Please draw a signature before locking." | Signature canvas is empty | Have the homeowner draw their signature |
| "Please lock the signature before submitting." | Signature was drawn but never locked | Tap Lock Signature before submitting |
| Yellow "X pending sync" badge | Work is saved locally, waiting for a connection | Get to a signal area — it will sync automatically |
| Red "FAILED — contact office" badge | An item is permanently stuck and needs help | Contact the office with the job details |
| "Offline Mode: Lead, photos, and signature saved locally." | You were offline when you submitted | No action needed — it will sync when connected |

---

## 10b. Image Analysis & Structured Forensic Flags

To assist you in presenting storm findings to homeowners and carriers, the app processes your uploaded job photos using Gemini's advanced multimodal vision model:
- **No-Math Photo Auditing**: The AI analysis focuses strictly on locating and identifying physical damage indicators. The model detects and logs forensic flags such as **hail impact marks**, **wind crease marks**, **granule loss**, and **exposed fiberglass**. 
- **Automated Evidence Grid**: These forensic flags and narratives populate the **Evidence Grid PDF** that you can use to prove storm damage to homeowners and adjusters. The AI does not compute any pricing or material costs; those are calculated mathematically by the system.
- **Image Bounds Check**: The confidence score assigned to each detected damage classification is validated to be strictly between 0% and 100%, rejecting any out-of-bounds or non-deterministic AI behavior.

---

## 11. What You Cannot Do (By Design)

For security, the field app strictly limits what canvassers can access:

- You cannot see or open jobs that aren't yours. The system checks your
  identity (via your PIN login) against the job's assigned canvasser on
  every request. Attempting to access another rep's job will result in a
  **403 Forbidden** error.
- You cannot access office, admin, or accounting screens.
- You cannot view other reps' commissions or job lists.
- You cannot see office-only documents (financials, commissions, QBO
  exports, carrier correspondence) — even if you know the document ID.
- You **can** see and download your own field-safe documents (photos,
  measurement reports, signed contingency agreements) via the "My Recent
  Jobs" section.

This is intentional — it protects homeowner data and keeps each rep's
work isolated and auditable.

---

## FAQ

**Q: Do I need to type my name every time I create a lead?**
No — the system already knows who you are from your PIN login and
attaches your identity automatically.

**Q: What if I lose signal right after I hit Submit?**
Nothing is lost. The entire lead — including photos and signature — is
saved as one package on your device and will sync automatically,
without creating a duplicate job, once you're back online.

**Q: A photo failed with an error message. Did I lose my lead, and will I have to re-upload everything?**
No to both. Your lead is safe, and any photos that already uploaded
successfully will NOT be sent again. Just tap **Submit Lead** — it only
retries the exact step that failed.

**Q: Can I take more than 15 photos?**
No, 15 is the maximum per job. If you try to add more, the app warns
you two ways: a toast message, and the photo counter briefly turning
red to show exactly how many were rejected.

**Q: What if the homeowner wants to redo their signature?**
Tap **✏️ Edit Signature** — it automatically clears the canvas so they
can draw a clean, fresh signature. This does not delete or restart the lead.

**Q: How do I know my offline lead actually made it to the office?**
Watch the pending-sync badge in the corner. Once it disappears (or the
count drops), it has successfully synced. If it ever turns red, that
means something needs office attention — it won't resolve itself.

**Q: Can I create a lead without a signature?**
Only for Retail Cash jobs. Insurance Restoration jobs require a locked
signature before you can submit.

**Q: Can I see my old jobs and their documents?**
Yes — scroll down past the New Lead form to the **"My Recent Jobs"**
section. You'll see your most recent jobs with the ability to load and
download your field-safe documents (photos, measurement reports, signed
contingency agreements).

**Q: Can I see the office's financial documents or supplements?**
No — those are classified as "office_only" and are strictly hidden from
field reps. You can only see documents marked as "field_safe."

**Q: I retried a failed submission a few times and it's still failing. What now?**
Stop retrying after 2–3 attempts. Note the job and the exact error
message, then contact the office — there may be a bad file or a larger
issue that needs manual review rather than repeated retries.

**Q: Who do I contact if something seems broken?**
Contact the office/Tech Admin if:

- You see a red "FAILED — contact office" badge.
- Retrying a failed submission doesn't resolve after a few tries.
- You believe a job was duplicated.
- Photos are consistently failing to upload.
- You're unsure whether a signature was captured correctly.

---

## 12. Storm Activity Monitor in the Field App

The field app contains a live **Storm Activity Monitor** widget located below the intake form.

### What it displays
- **Hail Events & Wind Events Counts**: These counts represent the total number of verified hail and wind reports recorded by the National Weather Service (NWS) within a 50-mile radius of the main office in the last 72 hours.
- **Storm-Target ZIPs**: Displays a list of top storm-impacted ZIP codes. Clicking any of these ZIP codes instantly filters your job list to show only leads within that area.
- **Recent Alerts List**: Displays the 5 most recent significant storm reports with details (county, magnitude like hail size/wind speed, and time).

### Actionable Rep Response
When you see a live, severe in-app banner alert (WebSocket toast) or see high event counts in specific areas on the Storm Activity Monitor card:
- **Target Canvassing & ZIP Filtering**: Filter your active job list by clicking a high-impact ZIP in the Storm-Target list to target your follow-ups. Look for the visual **☄️ HAIL** and **💨 WIND** indicators on your job cards.
- **ZIP Verification**: During intake or when resuming a lead, look for the inline storm risk warning block showing recent hail/wind activity for that ZIP code.
- **Leverage Evidence Grid**: Use the NOAA storm dates and report data to pitch to homeowners in that immediate area, establishing credibility and generating new leads.

---

## 13. AI Sales Tools & Neighbor Outreach Letters

To help close deals faster and streamline nearby jobsite canvassing, the Field App now integrates two powerful tools on job cards:

### 1. 🤖 AI Sales Tools Card
- **Personalized Narratives**: Tap the **Sales Tools** button on any of your active job cards.
- **Sales Summary**: Instantly generates a grounded, 2–3 sentence narrative summarizing the homeowner's status and nearby storm events. This provides a quick talking-point checklist before knocking.
- **Door-Knocking Script**: Generates a short, conversational door script referencing real nearby storm events (hail/wind sizes and dates) without any placeholder brackets, making it ready to pitch immediately.
- **Credit Conservation**: Generated tools are automatically cached securely in the document vault. Tap it again to retrieve the cached version instantly.

### 2. 🏘️ Neighbor Outreach Letter PDF
- **Dynamic Campaign Creation**: Once a job reaches **`INSTALL_COMPLETED`** status, a **Neighbor Letter** button will automatically appear on the job card.
- **Storm-Grounded Pitch**: Generates a professionally designed, single-page PDF letter featuring our corporate letterhead. The letter references the exact completed job address and highlights NWS storm events that occurred nearby.
- **Call-to-Action**: Invites neighbors to book a free roofing inspection. You can download and print these to drop at adjacent houses, maximizing lead gen around completed installs.

---

*This guide reflects the field app as of version `2.4.1`. If the app's
screens, buttons, or error messages change in a future update, this
guide should be reviewed and updated to match.*
